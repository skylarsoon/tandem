"""Serving real rooms: onboarding conversations, and planning searched from
what people confirmed (their airports, the trip brief) instead of a config file.

`serve` processes pending jobs in every room (or the given ones):
INTAKE/INTERPRET jobs go to the onboarding engine, PLAN jobs to the
coordinator with live research built per request. It runs inside the worker
service shell (service.py): one instance per state directory, woken by the
dispatcher when WORKER_WAKE_TOKEN is set, polling otherwise."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Literal
from pathlib import Path

from pydantic import Field

from .adapters.apify import ApifyFlightResearch, ApifyHTTP, RunLedger, TripResearch
from .adapters.claude import ClaudeModel
from .adapters.memo23 import ACTOR_ID, NORMALIZER_VERSION, MemoFilters, MemoSearch, build_input, normalize_offers
from .adapters.trip_actors import ACTORS, ActivitySearch, DinnerSearch, StaySearch, TransferSearch, TripSearches
from .budget import ProviderBudget
from .config import LiveLimits
from .contracts import ContractError, Record, Snapshot, digest
from .itinerary import pair_itineraries
from .live import CheckedResearch, Providers, trip_gateways
from .optimizer_bridge import make_solver
from .ports import SystemClock


class ServeConfig(Record):
    # model_calls and actor_runs are the whole service's ceilings per UTC day.
    limits: LiveLimits = Field(default_factory=lambda: LiveLimits(model_calls=500, actor_runs=40))
    # What one job may use (counted per job by the database): a 4-person
    # negotiation cycle alone is a decide and a voice call per person.
    job_model_calls: int = Field(default=100, ge=1, le=500)
    # How the group-dinner search is phrased; everything else comes from the room.
    dinner_query: str = "restaurant for large groups"
    # Who chooses the plan: the multi-selection optimizer (default) or TripSolver (minimax_v1).
    solver: Literal["minimax_v1", "cumulative_fairness_v1"] = "cumulative_fairness_v1"


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
                    "ARRIVAL_OVERLAP": MemoFilters(sort_by="arrival", max_stops=stops),
                    # They said no to everything found: look again, ordered differently (same hard filters).
                    "DECLINED": MemoFilters(sort_by="duration", max_stops=stops)}
        flights.append(MemoSearch(owner=route["owner"], origin=route["origin"], destination=route["destination"],
            departure_date=date.fromisoformat(route["date"]), origin_timezone=_airport(route["origin"])["tz"],
            destination_timezone=_airport(route["destination"])["tz"], initial_filters=MemoFilters(max_stops=stops),
            targeted_filters=targeted))
        if route.get("return_date"):  # the way home, searched one way and paired after (itinerary.py)
            flights.append(MemoSearch(owner=route["owner"], origin=route["destination"], destination=route["origin"],
                departure_date=date.fromisoformat(route["return_date"]), origin_timezone=_airport(route["destination"])["tz"],
                destination_timezone=_airport(route["origin"])["tz"], initial_filters=MemoFilters(max_stops=stops),
                targeted_filters=targeted, leg="return"))
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
        if "FLIGHT" in request.get("categories", ()) and not request.get("routes"):
            # Only onboarded rooms say where people fly from; a form-flow room needs
            # the operator's search config (`serve --config live.json --room N`).
            raise ContractError("ROOM_NOT_ONBOARDED")
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
        return pair_itineraries(TripResearch(gateway, shared).search(request))


def make_room_providers(config: ServeConfig, settings, directory, *, clock=None, claude_client=None, apify_client=None):
    settings.require()
    clock = clock or SystemClock()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    budget = ProviderBudget(directory / "providers.sqlite", digest(["serve", settings.claude_model, settings.apify_actor_build]),
        model_calls=config.limits.model_calls, actor_runs=config.limits.actor_runs, daily=True)
    model = ClaudeModel(settings.claude_model, settings.anthropic_api_key.get_secret_value(), client=claude_client,
        budget=budget, max_tokens=config.limits.max_output_tokens, timeout=config.limits.http_timeout)
    client = apify_client or ApifyHTTP(settings.apify_token.get_secret_value(), timeout=config.limits.http_timeout)
    ledger = RunLedger(directory / "runs.sqlite")
    research = CheckedResearch(RoomResearch(config, settings, client, ledger, budget, clock))
    return Providers(model, research, client, budget, ledger, settings.apify_actor_build, trip_categories=tuple(ACTORS))


def serve(config: ServeConfig, settings, directory, *, rooms=None, kinds=None, once=False, poll_seconds=3.0, max_jobs=None,
          parallel=6, settle_seconds=2.0, host="127.0.0.1", port=8080,
          clock=None, claude_client=None, apify_client=None, backend=None, providers=None, worker_id=None):
    """Process pending jobs until stopped (or one sweep with once=True)."""
    from .adapters.spacetime import SpacetimeBackend
    from .contracts import Limits
    from .runner import Worker
    from .service import WorkerService, run_service
    settings.require_backend()
    clock = clock or SystemClock()
    directory = Path(directory)
    providers = providers or make_room_providers(config, settings, directory, clock=clock,
                                                 claude_client=claude_client, apify_client=apify_client)
    if not providers.research.verified:
        providers.preflight()
    limits = Limits(model_calls=config.job_model_calls)
    backend = backend or SpacetimeBackend(settings.stdb_url, settings.stdb_database,
        settings.stdb_worker_token.get_secret_value(), clock=clock, limits=limits,
        owner_name=lambda member: member["display_name"])
    worker = Worker(backend, providers.model, providers.research, directory / "checkpoints.sqlite", clock=clock,
                    limits=limits, worker_id=worker_id or "serve-" + uuid.uuid4().hex[:8],
                    solver=make_solver(config.solver, limits.solver_seconds))
    results, handled = [], 0

    def run(job):
        try:
            return {"job": job["id"], "kind": job["kind"], "room": job["room_id"], **worker.run_one(str(job["id"]))}
        except ContractError as error:
            if error.code not in {"JOB_NOT_ELIGIBLE", "THREAD_ALREADY_RUNNING", "STALE_SNAPSHOT"}:
                raise
            return None

    def sweep():
        nonlocal handled
        # Profile jobs belong to no room, so a room filter doesn't hold them back.
        jobs = [j for j in backend.pending_jobs()
                if (rooms is None or j["room_id"] in rooms or j["kind"] == "PROFILE") and (kinds is None or j["kind"] in kinds)]
        if max_jobs is not None:
            jobs = jobs[:max(0, max_jobs - handled)]
        # Each person's onboarding job is independent (its own lease), so conversations run
        # side by side; planning jobs run one at a time.
        chats = [j for j in jobs if j["kind"] != "PLAN"]
        done = []
        with ThreadPoolExecutor(max_workers=max(1, min(parallel, len(chats) or 1))) as pool:
            done += [r for r in pool.map(run, chats) if r]
        done += [r for r in map(run, [j for j in jobs if j["kind"] == "PLAN"]) if r]
        handled += len(jobs)
        results.extend(done)
        return done

    if once or max_jobs is not None:
        while True:
            sweep()
            if once or handled >= max_jobs:
                return results
            time.sleep(poll_seconds)

    def scan(*_args, **_kwargs):
        done = sweep()
        return {"result": {"outcome": ",".join(sorted({r.get("outcome", "?") for r in done})) or "NO_ELIGIBLE_WORK"}}
    service = WorkerService(None, settings, directory, None, backend=backend, run=scan, rooms=rooms,
                            kinds=set(kinds) if kinds else {"PLAN", "INTAKE", "INTERPRET", "PROFILE"},
                            poll_interval=poll_seconds, settle_seconds=settle_seconds)
    token = settings.worker_wake_token.get_secret_value() if settings.worker_wake_token else None
    run_service(service, token, host=host, port=port, label={"mode": "ROOMS", "rooms": sorted(rooms) if rooms else "ALL"})
    return results
