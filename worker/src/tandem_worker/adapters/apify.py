"""Opt-in Apify flight gateway with resumable runs and bounded collection.

Actor-specific input/output mappings MUST be supplied after testing that Actor.
There is deliberately no guessed Google Flights scraper schema here.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import Field, ValidationError

from ..contracts import Candidate, ContractError, Record, digest


class ApprovedFlightSearch(Record):
    owner: str
    origin: str = Field(pattern=r"^[A-Z]{3}$")
    destination: str = Field(pattern=r"^[A-Z]{3}$")
    departure_date: date
    travelers: int = Field(default=1, gt=0, le=20)


class ApifyHTTP:
    """Only the fixed Apify API host; tokens stay in Authorization headers."""
    def __init__(self, token: str, *, timeout=30):
        if not token:
            raise ValueError("APIFY_TOKEN is required")
        self.token, self.timeout = token, timeout

    def _request(self, method, path, params=None, body=None):
        url = "https://api.apify.com/v2/" + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError):
            # Provider bodies/URLs may contain secrets or source text.
            raise ContractError("APIFY_REQUEST_FAILED") from None

    def start(self, actor_id, build, actor_input, max_charge_usd):
        return self._request("POST", f"acts/{actor_id}/runs",
            {"build": build, "maxTotalChargeUsd": max_charge_usd}, actor_input)["data"]

    def run(self, run_id):
        return self._request("GET", f"actor-runs/{run_id}")["data"]

    def items(self, dataset_id, offset, limit):
        return self._request("GET", f"datasets/{dataset_id}/items", {"format": "json", "offset": offset, "limit": limit})


class RunLedger:
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS apify_runs (key TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def read(self, key):
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT body FROM apify_runs WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def write(self, key, value):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT OR REPLACE INTO apify_runs VALUES (?, ?)", (key, json.dumps(value)))


class ApifyFlightResearch:
    def __init__(self, client, ledger: RunLedger, *, actor_id: str, build: str,
                 room_id: str, input_revision: int, permission_revision: int,
                 approved_searches: list[ApprovedFlightSearch],
                 build_input: Callable, normalize_item: Callable, clock,
                 max_polls=20, max_items=100, max_charge_usd=1.0, pause=time.sleep):
        if not re.fullmatch(r"[A-Za-z0-9_~-]+", actor_id) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", build):
            raise ValueError("Pin an allowlisted Actor ID and numeric build version")
        if max_polls <= 0 or max_items <= 0 or max_charge_usd <= 0:
            raise ValueError("Positive research budgets required")
        self.client, self.ledger, self.actor_id, self.build = client, ledger, actor_id, build
        self.room_id, self.input_revision, self.permission_revision = room_id, input_revision, permission_revision
        self.searches, self.build_input, self.normalize_item = approved_searches, build_input, normalize_item
        self.clock, self.pause = clock, pause
        self.max_polls, self.max_items, self.max_charge_usd = max_polls, max_items, max_charge_usd

    def search(self, request):
        if (request.get("room_id"), request.get("input_revision"), request.get("permission_revision")) != (self.room_id, self.input_revision, self.permission_revision):
            raise ContractError("STALE_SEARCH_AUTHORITY")
        if request.get("purpose") not in {"INITIAL", "TARGETED", "REFRESH"}:
            raise ContractError("INVALID_RESEARCH_PURPOSE")
        # The builder receives only confirmed route/date/count facts and purpose,
        # never the coordinator request, hidden caps or a model-authored query.
        results = []
        for search in self.searches:
            if search.owner not in request["members"]:
                continue
            actor_input = self.build_input(search, request["purpose"])
            key = digest([self.actor_id, self.build, request, search.model_dump(mode="json"), actor_input])
            run = self.ledger.read(key)
            if run is None:
                run = {"state": "STARTING", "polls": 0, "actor_id": self.actor_id, "build": self.build,
                       "request_digest": digest(actor_input), "purpose": request["purpose"]}
                self.ledger.write(key, run)
                # If acceptance succeeded but ID persistence was lost, STARTING
                # remains ambiguous. Never automatically resubmit a paid run.
                accepted = self.client.start(self.actor_id, self.build, actor_input, self.max_charge_usd)
                run.update(state="RUNNING", run_id=accepted["id"], dataset_id=accepted.get("defaultDatasetId"))
                self.ledger.write(key, run)
            if run["state"] == "STARTING":
                raise ContractError("APIFY_START_AMBIGUOUS")
            if run["state"] == "DONE":
                results.extend(Candidate.model_validate(c) for c in run["candidates"])
                continue
            while run["state"] != "COLLECTING" and run["polls"] < self.max_polls:
                run["polls"] += 1
                self.ledger.write(key, run)
                record = self.client.run(run["run_id"])
                if record["status"] == "SUCCEEDED":
                    run.update(state="COLLECTING", dataset_id=record["defaultDatasetId"], observed_at=self.clock.now())
                    self.ledger.write(key, run)
                    break
                if record["status"] in {"FAILED", "ABORTED", "TIMED-OUT"}:
                    raise ContractError("APIFY_RUN_FAILED")
                self.pause(min(2 ** (run["polls"] - 1), 10))
            if run["state"] != "COLLECTING":
                raise ContractError("APIFY_POLL_BUDGET_EXHAUSTED")
            normalized, quarantined = [], 0
            for offset in range(0, self.max_items, 50):
                page = self.client.items(run["dataset_id"], offset, min(50, self.max_items - offset))
                if not isinstance(page, list):
                    raise ContractError("APIFY_SCHEMA_CHANGED")
                for item in page[:min(50, self.max_items - offset)]:
                    try:
                        candidate = Candidate.model_validate(self.normalize_item(item, search, dict(run)))
                        if candidate.owner != search.owner or candidate.mode != "APIFY_OBSERVED" or candidate.run_ref != run["run_id"]:
                            raise ValueError("INVALID_PROVENANCE")
                        normalized.append(candidate)
                    except (ValidationError, ValueError, KeyError, TypeError):
                        quarantined += 1
                if len(page) < min(50, self.max_items - offset):
                    break
            run.update(state="DONE", candidates=[c.model_dump(mode="json") for c in normalized],
                       quarantined=quarantined, collection_limit=self.max_items)
            self.ledger.write(key, run)
            results.extend(normalized)
        return results
