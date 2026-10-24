"""Opt-in Apify research gateways with resumable runs and bounded collection.

Actor-specific input/output mappings MUST be supplied after testing that Actor
(see memo23.py for flights and trip_actors.py for stays, transfers and
suggestions). There is deliberately no guessed scraper schema here.

Every search first builds all Actor inputs (refusing before any paid start),
then starts every run, then polls them together and collects each as it
finishes, so N runs take about as long as the slowest one.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from dataclasses import dataclass, field
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

    def check_actor(self, actor_id, build, build_id, validate_schema=None):
        """Read-only preflight: token, pinned build and that build's input schema.
        The schema validator defaults to the one registered for this Actor."""
        validate_schema = validate_schema or schema_validator(actor_id)
        self._request("GET", "users/me")  # Public Actor metadata alone cannot validate a token.
        actor = self._request("GET", f"acts/{actor_id}")["data"]
        record = self._request("GET", f"actor-builds/{build_id}")["data"]
        if actor.get("id") != actor_id or record.get("buildNumber") != build or record.get("actId", record.get("actorId")) != actor_id or record.get("status") != "SUCCEEDED":
            raise ContractError("APIFY_BUILD_MISMATCH")
        schema = self._request("GET", f"actor-builds/{build_id}/openapi.json")
        validate_schema(schema)
        return {"actor_id": actor_id, "build": build, "build_id": build_id, "schema": "OK", "access": "OK"}

    def run(self, run_id):
        return self._request("GET", f"actor-runs/{run_id}")["data"]

    def run_with_timeout(self, run_id, timeout):
        return self._request("GET", f"actor-runs/{run_id}", timeout=min(timeout, self.timeout))["data"]

    def items(self, dataset_id, offset, limit):
        return self._request("GET", f"datasets/{dataset_id}/items", {"format": "json", "offset": offset, "limit": limit})

    def items_with_timeout(self, dataset_id, offset, limit, timeout):
        return self._request("GET", f"datasets/{dataset_id}/items", {"format": "json", "offset": offset, "limit": limit}, timeout=min(timeout, self.timeout))


def schema_validator(actor_id):
    from . import memo23, trip_actors
    validators = {memo23.ACTOR_ID: memo23.validate_input_schema, **trip_actors.SCHEMA_VALIDATORS}
    if actor_id not in validators:
        raise ContractError("APIFY_ACTOR_NOT_ALLOWLISTED")
    return validators[actor_id]


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
        fields = {"state", "category", "actor_id", "build", "request_digest", "purpose", "run_id", "dataset_id", "polls", "quarantined", "duplicates", "collection_limit", "collection_truncated"}
        return [{k: v for k, v in row.items() if k in fields} for row in rows]




CATEGORIES = ("FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER")


class Deadline:
    """One wall-clock budget shared by every run in a research step."""
    def __init__(self, monotonic, seconds):
        self.monotonic, self.at = monotonic, monotonic() + seconds

    def remaining(self):
        value = self.at - self.monotonic()
        if value <= 0:
            raise ContractError("APIFY_POLL_DEADLINE_EXCEEDED")
        return value


@dataclass
class RunJob:
    gateway: "ApifyResearch"
    key: str
    search: object
    actor_input: dict
    purpose: str
    run: dict | None = None
    results: list = field(default_factory=list)


class ApifyResearch:
    """One pinned Actor for one category. FLIGHT searches belong to an owner and
    run per targeted owner; the other categories have one shared search
    (owner None) that runs once for the group."""
    def __init__(self, client, ledger: RunLedger, *, actor_id: str, build: str,
                 room_id: str, input_revision: int, permission_revision: int,
                 approved_searches: list, build_input: Callable, normalize_item: Callable, clock,
                 category="FLIGHT", max_polls=20, max_items=100, max_charge_usd=1.0, pause=time.sleep,
                 budget=None, request_input_builder=None, poll_deadline_seconds=120, monotonic=time.monotonic,
                 normalizer_version=None):
        if not re.fullmatch(r"[A-Za-z0-9_~-]+", actor_id) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", build):
            raise ValueError("Pin an allowlisted Actor ID and numeric build version")
        if max_polls <= 0 or max_items <= 0 or max_charge_usd <= 0:
            raise ValueError("Positive research budgets required")
        if category not in CATEGORIES:
            raise ValueError("Unknown research category")
        if any((getattr(s, "owner", None) is None) != (category != "FLIGHT") for s in approved_searches):
            raise ValueError("Flight searches need an owner; shared searches must not have one")
        self.client, self.ledger, self.actor_id, self.build = client, ledger, actor_id, build
        self.category = category
        self.room_id, self.input_revision, self.permission_revision = room_id, input_revision, permission_revision
        self.searches, self.build_input, self.normalize_item = approved_searches, build_input, normalize_item
        self.clock, self.pause = clock, pause
        self.max_polls, self.max_items, self.max_charge_usd = max_polls, max_items, max_charge_usd
        self.budget, self.request_input_builder = budget, request_input_builder
        self.poll_deadline_seconds, self.monotonic = poll_deadline_seconds, monotonic
        self.normalizer_version = normalizer_version

    def search(self, request):
        jobs = self.prepare(request)
        deadline = Deadline(self.monotonic, self.poll_deadline_seconds)
        self.precheck(jobs)
        self.launch(jobs, deadline)
        return collect_all(jobs, deadline, self.pause)

    def prepare(self, request):
        """Authorize and build every Actor input. Raises before any paid start."""
        if (request.get("room_id"), request.get("input_revision"), request.get("permission_revision")) != (self.room_id, self.input_revision, self.permission_revision):
            raise ContractError("STALE_SEARCH_AUTHORITY")
        if request.get("purpose") not in {"INITIAL", "TARGETED", "REFRESH"}:
            raise ContractError("INVALID_RESEARCH_PURPOSE")
        targets = request.get("target_owners", request["members"])
        if not targets or not set(targets) <= set(request["members"]):
            raise ContractError("NOT_AUTHORIZED")
        # The builder receives only confirmed search facts and purpose, never
        # the coordinator request's hidden caps or a model-authored query.
        jobs = []
        for search in self.searches:
            owner = getattr(search, "owner", None)
            if owner is not None and owner not in targets:
                continue
            actor_input = self.request_input_builder(search, request) if self.request_input_builder else self.build_input(search, request["purpose"])
            key = digest([self.actor_id, self.build, request, search.model_dump(mode="json"), actor_input])
            jobs.append(RunJob(self, key, search, actor_input, request["purpose"]))
        return jobs

    @staticmethod
    def refuse(run):
        if run is None:
            return
        if run["state"] == "BUDGET_BLOCKED":
            raise ContractError("ACTOR_RUN_BUDGET_EXHAUSTED")
        if run["state"] == "STARTING":
            raise ContractError("APIFY_START_AMBIGUOUS")

    def precheck(self, jobs):
        """Refuse before starting anything new if an earlier start is unresolved."""
        for job in jobs:
            self.refuse(self.ledger.read(job.key))

    def launch(self, jobs, deadline):
        for job in jobs:
            run = self.ledger.read(job.key)
            if run is None:
                run = {"state": "STARTING", "polls": 0, "category": self.category, "actor_id": self.actor_id,
                       "build": self.build, "request_digest": digest(job.actor_input), "purpose": job.purpose}
                created, run = self.ledger.begin(job.key, run)
                # If acceptance succeeded but ID persistence was lost, STARTING
                # remains ambiguous. Never automatically resubmit a paid run.
                if created:
                    deadline.remaining()
                    if self.budget:
                        try:
                            self.budget.reserve("apify")
                        except ContractError:
                            run["state"] = "BUDGET_BLOCKED"
                            self.ledger.write(job.key, run)
                            raise
                    accepted = self.client.start(self.actor_id, self.build, job.actor_input, self.max_charge_usd)
                    run.update(state="RUNNING", run_id=accepted["id"], dataset_id=accepted.get("defaultDatasetId"))
                    self.ledger.write(job.key, run)
            self.refuse(run)
            if run["state"] == "DONE" and self.normalizer_version and run.get("normalizer_version") != self.normalizer_version:
                # Reprocess the existing dataset after a mapper correction;
                # never start another paid run or refresh observation time.
                run["state"] = "COLLECTING"
                self.ledger.write(job.key, run)
            job.run = run

    def poll(self, job, deadline):
        """One status read. True once the run's dataset is ready to collect."""
        run = job.run
        if run["polls"] >= self.max_polls:
            raise ContractError("APIFY_POLL_BUDGET_EXHAUSTED")
        run["polls"] += 1
        self.ledger.write(job.key, run)
        timeout = deadline.remaining()
        record = self.client.run_with_timeout(run["run_id"], timeout) if hasattr(self.client, "run_with_timeout") else self.client.run(run["run_id"])
        if record["status"] == "SUCCEEDED":
            run.update(state="COLLECTING", dataset_id=record["defaultDatasetId"], observed_at=self.clock.now())
            self.ledger.write(job.key, run)
            return True
        if record["status"] in {"FAILED", "ABORTED", "TIMED-OUT"}:
            raise ContractError("APIFY_RUN_FAILED")
        return False

    def collect(self, job, deadline):
        run, search = job.run, job.search
        owner = getattr(search, "owner", None)
        normalized, quarantined, duplicates = [], 0, 0
        seen = set()
        truncated = False
        for offset in range(0, self.max_items, 50):
            size = min(50, self.max_items - offset)
            timeout = deadline.remaining()
            page = self.client.items_with_timeout(run["dataset_id"], offset, size, timeout) if hasattr(self.client, "items_with_timeout") else self.client.items(run["dataset_id"], offset, size)
            if not isinstance(page, list):
                raise ContractError("APIFY_SCHEMA_CHANGED")
            for item in page[:size]:
                try:
                    produced = self.normalize_item(item, search, dict(run))
                    # A row may yield several priced variants (e.g. with a checked bag).
                    candidates = [Candidate.model_validate(c) for c in (produced if isinstance(produced, list) else [produced])]
                    if any(c.owner != owner or c.category != self.category or c.mode != "APIFY_OBSERVED"
                           or c.run_ref != run["run_id"] for c in candidates):
                        raise ValueError("INVALID_PROVENANCE")
                    for candidate in candidates:
                        if candidate.id in seen:
                            duplicates += 1
                            continue
                        seen.add(candidate.id)
                        normalized.append(candidate)
                except (ValidationError, ValueError, KeyError, TypeError, AttributeError):
                    quarantined += 1
            if len(page) < size:
                break
            truncated = offset + size >= self.max_items
        run.update(state="DONE", candidates=[c.model_dump(mode="json") for c in normalized],
                   quarantined=quarantined, duplicates=duplicates, collection_limit=self.max_items, collection_truncated=truncated,
                   normalizer_version=self.normalizer_version)
        self.ledger.write(job.key, run)
        job.results = normalized


class ApifyFlightResearch(ApifyResearch):
    """Per-owner flight searches (the original gateway)."""
    def __init__(self, client, ledger, **kwargs):
        if kwargs.setdefault("category", "FLIGHT") != "FLIGHT":
            raise ValueError("Flight research is FLIGHT only")
        super().__init__(client, ledger, **kwargs)


def collect_all(jobs, deadline, pause):
    """Poll every started run in turn and collect each as soon as it succeeds.
    Each run keeps its own durable poll count and ledger state."""
    pending = []
    for job in jobs:
        if job.run["state"] == "DONE":
            job.results = [Candidate.model_validate(c) for c in job.run["candidates"]]
        elif job.run["state"] == "COLLECTING":
            job.gateway.collect(job, deadline)
        else:
            pending.append(job)
    cycle = 0
    while pending:
        cycle += 1
        for job in list(pending):
            if job.gateway.poll(job, deadline):
                job.gateway.collect(job, deadline)
                pending.remove(job)
        if pending:
            pause(min(2 ** (cycle - 1), 10, deadline.remaining()))
    return [candidate for job in jobs for candidate in job.results]


class TripResearch:
    """Routes one research request to the flight gateway and the shared
    category gateways, starting every run before polling any.

    `request["categories"]` lists what the coordinator would like; categories
    with no configured search are skipped. A TARGETED request runs only the
    categories whose approved search can act on its blocker."""
    def __init__(self, flights: ApifyFlightResearch, shared: dict, *, poll_deadline_seconds=None, pause=None, monotonic=None):
        if not isinstance(flights, ApifyFlightResearch):
            raise ValueError("Flight research is required")
        for category, gateway in shared.items():
            if category not in CATEGORIES or category == "FLIGHT" or gateway.category != category:
                raise ValueError("Shared gateways must match their category")
        self.flights, self.shared = flights, dict(shared)
        self.poll_deadline_seconds = poll_deadline_seconds or flights.poll_deadline_seconds
        self.pause, self.monotonic = pause or flights.pause, monotonic or flights.monotonic

    def gateways(self, request):
        categories = tuple(request.get("categories") or ("FLIGHT",))
        if len(set(categories)) != len(categories) or not set(categories) <= set(CATEGORIES):
            raise ContractError("INVALID_RESEARCH_CATEGORIES")
        if request.get("scope", "FLIGHTS_ONLY") == "FLIGHTS_ONLY" and set(categories) != {"FLIGHT"}:
            raise ContractError("RESEARCH_SCOPE_MISMATCH")
        routed = {"FLIGHT": self.flights, **self.shared}
        return [routed[c] for c in CATEGORIES if c in categories and c in routed]

    def search(self, request):
        gateways = self.gateways(request)
        jobs, refused = [], 0
        for gateway in gateways:
            try:
                jobs += gateway.prepare(request)
            except ContractError as error:
                # A targeted search reruns only what can act on the blocker.
                if request.get("purpose") != "TARGETED" or error.code != "TARGETED_SEARCH_NOT_AUTHORIZED":
                    raise
                refused += 1
        if gateways and refused == len(gateways):
            raise ContractError("TARGETED_SEARCH_NOT_AUTHORIZED")
        deadline = Deadline(self.monotonic, self.poll_deadline_seconds)
        for job in jobs:
            job.gateway.precheck([job])
        for gateway in gateways:
            gateway.launch([j for j in jobs if j.gateway is gateway], deadline)
        return collect_all(jobs, deadline, self.pause)
