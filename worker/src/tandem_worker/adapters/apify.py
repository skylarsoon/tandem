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

    def _request(self, method, path, params=None, body=None, *, timeout=None):
        url = "https://api.apify.com/v2/" + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        for attempt in range(3 if method == "GET" else 1):
            try:
                with urlopen(request, timeout=timeout or self.timeout) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code in (401, 403):
                    code = "APIFY_AUTH_FAILED"
                elif error.code == 404:
                    code = "APIFY_RESOURCE_UNAVAILABLE"
                elif error.code == 429:
                    code = "APIFY_RATE_LIMITED"
                else:
                    code = "APIFY_REQUEST_FAILED"
                transient = error.code == 429 or error.code >= 500
            except (URLError, TimeoutError):
                code, transient = "APIFY_CONNECTION_FAILED", True
            except ValueError:
                code, transient = "APIFY_INVALID_RESPONSE", False
            # Explicit poll deadlines own retries on bounded run/dataset reads.
            if transient and attempt < 2 and method == "GET" and timeout is None:
                time.sleep(2 ** attempt)
                continue
            raise ContractError(code) from None

    def start(self, actor_id, build, actor_input, max_charge_usd):
        return self._request("POST", f"acts/{actor_id}/runs",
            {"build": build, "maxTotalChargeUsd": max_charge_usd, "timeout": 120}, actor_input)["data"]

    def check_actor(self, actor_id, build, build_id):
        from .memo23 import validate_input_schema
        self._request("GET", "users/me")  # Public Actor metadata alone cannot validate a token.
        actor = self._request("GET", f"acts/{actor_id}")["data"]
        record = self._request("GET", f"actor-builds/{build_id}")["data"]
        if actor.get("id") != actor_id or record.get("buildNumber") != build or record.get("actId", record.get("actorId")) != actor_id or record.get("status") != "SUCCEEDED":
            raise ContractError("APIFY_BUILD_MISMATCH")
        schema = self._request("GET", f"actor-builds/{build_id}/openapi.json")
        validate_input_schema(schema)
        return {"actor_id": actor_id, "build": build, "build_id": build_id, "schema": "OK", "access": "OK"}

    def run(self, run_id):
        return self._request("GET", f"actor-runs/{run_id}")["data"]

    def run_with_timeout(self, run_id, timeout):
        return self._request("GET", f"actor-runs/{run_id}", timeout=min(timeout, self.timeout))["data"]

    def items(self, dataset_id, offset, limit):
        return self._request("GET", f"datasets/{dataset_id}/items", {"format": "json", "offset": offset, "limit": limit})

    def items_with_timeout(self, dataset_id, offset, limit, timeout):
        return self._request("GET", f"datasets/{dataset_id}/items", {"format": "json", "offset": offset, "limit": limit}, timeout=min(timeout, self.timeout))


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

    def begin(self, key, value):
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body FROM apify_runs WHERE key=?", (key,)).fetchone()
            if row:
                return False, json.loads(row[0])
            conn.execute("INSERT INTO apify_runs VALUES (?, ?)", (key, json.dumps(value)))
            return True, value

    def report(self):
        with sqlite3.connect(self.path) as conn:
            rows = [json.loads(r[0]) for r in conn.execute("SELECT body FROM apify_runs ORDER BY rowid")]
        fields = {"state", "actor_id", "build", "request_digest", "purpose", "run_id", "dataset_id", "polls", "quarantined", "duplicates", "collection_limit", "collection_truncated"}
        return [{k: v for k, v in row.items() if k in fields} for row in rows]


class ApifyFlightResearch:
    def __init__(self, client, ledger: RunLedger, *, actor_id: str, build: str,
                 room_id: str, input_revision: int, permission_revision: int,
                 approved_searches: list[ApprovedFlightSearch],
                 build_input: Callable, normalize_item: Callable, clock,
                 max_polls=20, max_items=100, max_charge_usd=1.0, pause=time.sleep,
                 budget=None, request_input_builder=None, poll_deadline_seconds=120, monotonic=time.monotonic,
                 normalizer_version=None):
        if not re.fullmatch(r"[A-Za-z0-9_~-]+", actor_id) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", build):
            raise ValueError("Pin an allowlisted Actor ID and numeric build version")
        if max_polls <= 0 or max_items <= 0 or max_charge_usd <= 0:
            raise ValueError("Positive research budgets required")
        self.client, self.ledger, self.actor_id, self.build = client, ledger, actor_id, build
        self.room_id, self.input_revision, self.permission_revision = room_id, input_revision, permission_revision
        self.searches, self.build_input, self.normalize_item = approved_searches, build_input, normalize_item
        self.clock, self.pause = clock, pause
        self.max_polls, self.max_items, self.max_charge_usd = max_polls, max_items, max_charge_usd
        self.budget, self.request_input_builder = budget, request_input_builder
        self.poll_deadline_seconds, self.monotonic = poll_deadline_seconds, monotonic
        self.normalizer_version = normalizer_version

    def search(self, request):
        if (request.get("room_id"), request.get("input_revision"), request.get("permission_revision")) != (self.room_id, self.input_revision, self.permission_revision):
            raise ContractError("STALE_SEARCH_AUTHORITY")
        if request.get("purpose") not in {"INITIAL", "TARGETED", "REFRESH"}:
            raise ContractError("INVALID_RESEARCH_PURPOSE")
        targets = request.get("target_owners", request["members"])
        if not targets or not set(targets) <= set(request["members"]):
            raise ContractError("NOT_AUTHORIZED")
        deadline = self.monotonic() + self.poll_deadline_seconds

        def remaining():
            value = deadline - self.monotonic()
            if value <= 0:
                raise ContractError("APIFY_POLL_DEADLINE_EXCEEDED")
            return value
        # The builder receives only confirmed route/date/count facts and purpose,
        # never the coordinator request, hidden caps or a model-authored query.
        results = []
        for search in self.searches:
            if search.owner not in targets:
                continue
            actor_input = self.request_input_builder(search, request) if self.request_input_builder else self.build_input(search, request["purpose"])
            key = digest([self.actor_id, self.build, request, search.model_dump(mode="json"), actor_input])
            run = self.ledger.read(key)
            if run is None:
                run = {"state": "STARTING", "polls": 0, "actor_id": self.actor_id, "build": self.build,
                       "request_digest": digest(actor_input), "purpose": request["purpose"]}
                created, run = self.ledger.begin(key, run)
                # If acceptance succeeded but ID persistence was lost, STARTING
                # remains ambiguous. Never automatically resubmit a paid run.
                if created:
                    remaining()
                    if self.budget:
                        try:
                            self.budget.reserve("apify")
                        except ContractError:
                            run["state"] = "BUDGET_BLOCKED"
                            self.ledger.write(key, run)
                            raise
                    accepted = self.client.start(self.actor_id, self.build, actor_input, self.max_charge_usd)
                    run.update(state="RUNNING", run_id=accepted["id"], dataset_id=accepted.get("defaultDatasetId"))
                    self.ledger.write(key, run)
            if run["state"] == "BUDGET_BLOCKED":
                raise ContractError("ACTOR_RUN_BUDGET_EXHAUSTED")
            if run["state"] == "STARTING":
                raise ContractError("APIFY_START_AMBIGUOUS")
            if run["state"] == "DONE" and self.normalizer_version and run.get("normalizer_version") != self.normalizer_version:
                # Reprocess the existing dataset after a mapper correction;
                # never start another paid run or refresh observation time.
                run["state"] = "COLLECTING"
                self.ledger.write(key, run)
            if run["state"] == "DONE":
                results.extend(Candidate.model_validate(c) for c in run["candidates"])
                continue
            while run["state"] != "COLLECTING" and run["polls"] < self.max_polls:
                run["polls"] += 1
                self.ledger.write(key, run)
                timeout = remaining()
                record = self.client.run_with_timeout(run["run_id"], timeout) if hasattr(self.client, "run_with_timeout") else self.client.run(run["run_id"])
                if record["status"] == "SUCCEEDED":
                    run.update(state="COLLECTING", dataset_id=record["defaultDatasetId"], observed_at=self.clock.now())
                    self.ledger.write(key, run)
                    break
                if record["status"] in {"FAILED", "ABORTED", "TIMED-OUT"}:
                    raise ContractError("APIFY_RUN_FAILED")
                self.pause(min(2 ** (run["polls"] - 1), 10, remaining()))
            if run["state"] != "COLLECTING":
                raise ContractError("APIFY_POLL_BUDGET_EXHAUSTED")
            normalized, quarantined, duplicates = [], 0, 0
            seen = set()
            truncated = False
            for offset in range(0, self.max_items, 50):
                size = min(50, self.max_items - offset)
                timeout = remaining()
                page = self.client.items_with_timeout(run["dataset_id"], offset, size, timeout) if hasattr(self.client, "items_with_timeout") else self.client.items(run["dataset_id"], offset, size)
                if not isinstance(page, list):
                    raise ContractError("APIFY_SCHEMA_CHANGED")
                for item in page[:min(50, self.max_items - offset)]:
                    try:
                        produced = self.normalize_item(item, search, dict(run))
                        # A row may yield several priced variants (e.g. with a checked bag).
                        candidates = [Candidate.model_validate(c) for c in (produced if isinstance(produced, list) else [produced])]
                        if any(c.owner != search.owner or c.mode != "APIFY_OBSERVED" or c.run_ref != run["run_id"] for c in candidates):
                            raise ValueError("INVALID_PROVENANCE")
                        for candidate in candidates:
                            if candidate.id in seen:
                                duplicates += 1
                                continue
                            seen.add(candidate.id)
                            normalized.append(candidate)
                    except (ValidationError, ValueError, KeyError, TypeError):
                        quarantined += 1
                if len(page) < min(50, self.max_items - offset):
                    break
                truncated = offset + size >= self.max_items
            run.update(state="DONE", candidates=[c.model_dump(mode="json") for c in normalized],
                       quarantined=quarantined, duplicates=duplicates, collection_limit=self.max_items, collection_truncated=truncated,
                       normalizer_version=self.normalizer_version)
            self.ledger.write(key, run)
            results.extend(normalized)
        return results
