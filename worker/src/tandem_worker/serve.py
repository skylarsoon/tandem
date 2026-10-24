"""Serving real rooms: onboarding conversations, and planning searched from
what people confirmed (their airports, the trip brief) instead of a config file.

`serve` polls SpacetimeDB for pending jobs in every room (or the given ones):
INTAKE/INTERPRET jobs go to the onboarding engine, PLAN jobs to the
coordinator with live research built per request."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from pydantic import Field

from .adapters.apify import ApifyFlightResearch, ApifyHTTP, RunLedger, TripResearch
from .adapters.claude import ClaudeModel
from .adapters.memo23 import ACTOR_ID, NORMALIZER_VERSION, MemoFilters, MemoSearch, build_input, normalize_offers
from .adapters.trip_actors import ACTORS, ActivitySearch, DinnerSearch, StaySearch, TransferSearch, TripSearches
from .budget import ProviderBudget
from .config import LiveLimits
from .contracts import ContractError, Record, Snapshot, digest
from .live import CheckedResearch, Providers, trip_gateways
from .ports import SystemClock


class ServeConfig(Record):
    limits: LiveLimits = Field(default_factory=LiveLimits)
    # How the group-dinner search is phrased; everything else comes from the room.
    dinner_query: str = "restaurant for large groups"


def _airport(code: str) -> dict:
    import airportsdata
    found = airportsdata.load("IATA").get(code)
    if not found:
        raise ContractError("UNKNOWN_AIRPORT")
    return found


def searches_for(request: dict, dinner_query: str) -> tuple[list[MemoSearch], TripSearches]:
    """This request's searches, from people's confirmed airports and the trip brief."""
    flights = []
    for route in request.get("routes") or ():
        stops = route.get("max_stops")
        stops = stops if stops is not None and stops <= 2 else None  # the actor's own maximum filter is 2
        targeted = {"HARD_LIMIT": MemoFilters(sort_by="price", max_stops=stops),
                    "NO_MATCHING_CANDIDATE": MemoFilters(sort_by="price", max_stops=stops),
                    "BAGGAGE_UNKNOWN": MemoFilters(sort_by="price", checked_bags=1, max_stops=stops),
                    "ARRIVAL_OVERLAP": MemoFilters(sort_by="arrival", max_stops=stops)}
        flights.append(MemoSearch(owner=route["owner"], origin=route["origin"], destination=route["destination"],
            departure_date=date.fromisoformat(route["date"]), origin_timezone=_airport(route["origin"])["tz"],
            destination_timezone=_airport(route["destination"])["tz"], initial_filters=MemoFilters(max_stops=stops),
            targeted_filters=targeted))
    trip, shared = request.get("trip"), {}
    if trip:
        airport = _airport(request["routes"][0]["destination"]) if request.get("routes") else None
        check_in = date.fromisoformat(trip["check_in"])
        shared["stay"] = StaySearch(destination=trip["destination"], check_in=check_in,
                                    check_out=date.fromisoformat(trip["check_out"]), adults=trip["party_size"])
        if trip.get("pickup_time") and airport:
            shared["transfer"] = TransferSearch(pickup_location=airport["name"], dropoff_location=trip["destination"],
                pickup_date=check_in, pickup_time=trip["pickup_time"], passengers=trip["party_size"])
        if airport:
            shared["activities"] = ActivitySearch(destination=f"{trip['destination']}, {airport['country'].lower()}",
                                                  date=check_in + timedelta(days=1))
            shared["dinner"] = DinnerSearch(query=dinner_query,
                location=", ".join(x for x in (trip["destination"], airport.get("subd"), airport["country"]) if x))
    return flights, TripSearches(**shared)


class RoomResearch:
    """Builds each request's searches from the room, then runs them through the
    same guarded gateways (ledger, budget, deadline, concurrency) as config runs."""
    def __init__(self, config: ServeConfig, settings, client, ledger, budget, clock):
        self.config, self.settings, self.client, self.ledger, self.budget, self.clock = config, settings, client, ledger, budget, clock

    def search(self, request):
        flights, trip = searches_for(request, self.config.dinner_query)
        limits = self.config.limits
        snapshot = Snapshot(room_id=request["room_id"], members=tuple(request["members"]),
                            input_revision=request["input_revision"], permission_revision=request["permission_revision"])
        gateway = ApifyFlightResearch(self.client, self.ledger, actor_id=ACTOR_ID, build=self.settings.apify_actor_build,
            room_id=snapshot.room_id, input_revision=snapshot.input_revision, permission_revision=snapshot.permission_revision,
            approved_searches=flights, clock=self.clock, budget=self.budget,
            build_input=lambda search, purpose: build_input(search, {"purpose": purpose}, limits.max_items),
            request_input_builder=lambda search, request: build_input(search, request, limits.max_items),
            normalize_item=lambda item, search, run: normalize_offers(item, search, run, freshness_seconds=limits.freshness_seconds),
            max_items=limits.max_items, max_charge_usd=limits.max_charge_usd, poll_deadline_seconds=limits.poll_deadline_seconds,
            normalizer_version=NORMALIZER_VERSION, max_concurrent_runs=limits.max_concurrent_runs)
        config = type("Trip", (), {"trip": trip, "limits": limits})
        shared = trip_gateways(config, self.client, self.ledger, snapshot, self.clock, self.budget)
        return TripResearch(gateway, shared).search(request)


def make_room_providers(config: ServeConfig, settings, directory, *, clock=None, claude_client=None, apify_client=None):
    settings.require()
    clock = clock or SystemClock()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    budget = ProviderBudget(directory / "providers.sqlite", digest(["serve", settings.claude_model, settings.apify_actor_build]),
        model_calls=config.limits.model_calls, actor_runs=config.limits.actor_runs)
    model = ClaudeModel(settings.claude_model, settings.anthropic_api_key.get_secret_value(), client=claude_client,
        budget=budget, max_tokens=config.limits.max_output_tokens, timeout=config.limits.http_timeout)
    client = apify_client or ApifyHTTP(settings.apify_token.get_secret_value(), timeout=config.limits.http_timeout)
    ledger = RunLedger(directory / "runs.sqlite")
    research = CheckedResearch(RoomResearch(config, settings, client, ledger, budget, clock))
    return Providers(model, research, client, budget, ledger, settings.apify_actor_build, trip_categories=tuple(ACTORS))


def serve(config: ServeConfig, settings, directory, *, rooms=None, kinds=None, once=False, poll_seconds=3.0, max_jobs=None,
          parallel=6,
          clock=None, claude_client=None, apify_client=None, backend=None, providers=None, worker_id=None):
    """Process pending jobs until stopped (or one sweep with once=True)."""
    from .adapters.spacetime import SpacetimeBackend
    from .contracts import Limits
    from .runner import Worker
    settings.require_backend()
    clock = clock or SystemClock()
    directory = Path(directory)
    providers = providers or make_room_providers(config, settings, directory, clock=clock,
                                                 claude_client=claude_client, apify_client=apify_client)
    if not providers.research.verified:
        providers.preflight()
    limits = Limits(model_calls=config.limits.model_calls)
    backend = backend or SpacetimeBackend(settings.stdb_url, settings.stdb_database,
        settings.stdb_worker_token.get_secret_value(), clock=clock, limits=limits,
        owner_name=lambda member: member["display_name"])
    worker = Worker(backend, providers.model, providers.research, directory / "checkpoints.sqlite", clock=clock,
                    limits=limits, worker_id=worker_id or "serve-" + uuid.uuid4().hex[:8])
    results, handled = [], 0

    def run(job):
        try:
            return {"job": job["id"], "kind": job["kind"], "room": job["room_id"], **worker.run_one(str(job["id"]))}
        except ContractError as error:
            if error.code not in {"JOB_NOT_ELIGIBLE", "THREAD_ALREADY_RUNNING", "STALE_SNAPSHOT"}:
                raise
            return None

    while True:
        jobs = [j for j in backend.pending_jobs()
                if (rooms is None or j["room_id"] in rooms) and (kinds is None or j["kind"] in kinds)]
        if max_jobs is not None:
            jobs = jobs[:max(0, max_jobs - handled)]
        # Each person's onboarding job is independent (its own lease), so conversations run
        # side by side; planning jobs run one at a time.
        chats = [j for j in jobs if j["kind"] != "PLAN"]
        with ThreadPoolExecutor(max_workers=max(1, min(parallel, len(chats) or 1))) as pool:
            results += [r for r in pool.map(run, chats) if r]
        results += [r for r in map(run, [j for j in jobs if j["kind"] == "PLAN"]) if r]
        handled += len(jobs)
        if max_jobs is not None and handled >= max_jobs:
            return results
        if once:
            return results
        time.sleep(poll_seconds)
