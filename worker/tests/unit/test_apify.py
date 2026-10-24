from datetime import date

import pytest

from tandem_worker.adapters.apify import ApprovedFlightSearch, ApifyFlightResearch, RunLedger
from tandem_worker.contracts import Candidate, ContractError
from tandem_worker.ports import FakeClock


class Client:
    def __init__(self):
        self.starts = 0
        self.polls = 0
        self.pages = []

    def start(self, *args):
        self.starts += 1
        return {"id": "run1", "defaultDatasetId": "dataset1"}

    def run(self, run_id):
        self.polls += 1
        return {"status": "SUCCEEDED", "defaultDatasetId": "dataset1"}

    def items(self, dataset_id, offset, limit):
        self.pages.append((offset, limit))
        return [{"price": 42000}] if offset == 0 else []


def setup_gateway(tmp_path, client=None, max_polls=2):
    clock = FakeClock()
    client = client or Client()
    def normalize(item, search, run):
        return Candidate(id="observed-1", owner=search.owner, total_cents=item["price"], arrival_epoch=1800010000,
            observed_at=run["observed_at"], valid_until=run["observed_at"] + 60,
            source_url="https://source.invalid/flight", run_ref=run["run_id"], mode="APIFY_OBSERVED", cost_basis="COMPLETE_FLIGHT_TOTAL")
    gateway = ApifyFlightResearch(client, RunLedger(tmp_path / "runs.sqlite"), actor_id="allowlisted-actor", build="1.2.3",
        room_id="r", input_revision=1, permission_revision=1, clock=clock,
        approved_searches=[ApprovedFlightSearch(owner="p1", origin="SFO", destination="LAX", departure_date=date(2027, 1, 1))],
        build_input=lambda search, purpose: {"origin": search.origin, "destination": search.destination, "purpose": purpose},
        normalize_item=normalize, max_polls=max_polls, pause=lambda seconds: None)
    request = {"room_id": "r", "input_revision": 1, "permission_revision": 1, "members": ["p1"], "purpose": "INITIAL"}
    return gateway, client, request


def test_run_id_and_dataset_reused_without_second_submission(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    results = gateway.search(request)
    assert gateway.search(request) == results
    assert client.starts == 1
    assert results[0].mode == "APIFY_OBSERVED"
    assert results[0].run_ref == "run1"


def test_ambiguous_acceptance_never_automatically_starts_again(tmp_path):
    class Ambiguous(Client):
        def start(self, *args):
            self.starts += 1
            raise SystemExit()
    gateway, client, request = setup_gateway(tmp_path, Ambiguous())
    with pytest.raises(SystemExit):
        gateway.search(request)
    with pytest.raises(ContractError, match="APIFY_START_AMBIGUOUS"):
        gateway.search(request)
    assert client.starts == 1


def test_known_run_is_resumed_after_dataset_failure(tmp_path):
    class FailOnce(Client):
        def items(self, *args):
            if not self.pages:
                self.pages.append("failed")
                raise RuntimeError("synthetic failure")
            return super().items(*args)
    gateway, client, request = setup_gateway(tmp_path, FailOnce(), max_polls=1)
    with pytest.raises(RuntimeError):
        gateway.search(request)
    assert gateway.search(request)
    assert client.starts == client.polls == 1


def test_poll_budget_survives_repeated_calls(tmp_path):
    class Running(Client):
        def run(self, run_id):
            self.polls += 1
            return {"status": "RUNNING"}
    gateway, client, request = setup_gateway(tmp_path, Running())
    for _ in range(2):
        with pytest.raises(ContractError, match="APIFY_POLL_BUDGET_EXHAUSTED"):
            gateway.search(request)
    assert client.starts == 1
    assert client.polls == 2


def test_search_does_not_receive_private_reason_or_cap(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    seen = []
    gateway.build_input = lambda search, purpose: seen.append(search.model_dump()) or {"origin": search.origin}
    gateway.search({**request, "private_reason": "INJECTED", "hard_cap": 1})
    assert "private_reason" not in seen[0] and "hard_cap" not in seen[0]


def test_stale_search_authority_rejected_before_external_call(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    with pytest.raises(ContractError, match="STALE_SEARCH_AUTHORITY"):
        gateway.search({**request, "permission_revision": 2})
    assert client.starts == 0


def test_bad_actor_item_is_quarantined_not_fabricated(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    gateway.normalize_item = lambda *args: {"unexpected": "schema changed"}
    assert gateway.search(request) == []


def test_dataset_pagination_is_bounded_and_duplicate_records_are_deduplicated(tmp_path):
    class Paged(Client):
        def items(self, dataset_id, offset, limit):
            self.pages.append((offset, limit))
            return [{"price": 42000}] * max(0, min(limit, 75 - offset))
    gateway, client, request = setup_gateway(tmp_path, Paged())
    results = gateway.search(request)
    assert len(results) == 1
    assert client.pages == [(0, 50), (50, 50)]
    report = gateway.ledger.report()[0]
    assert report["duplicates"] == 74
    assert report["collection_truncated"] is False


def test_polling_deadline_stops_without_starting_another_run(tmp_path):
    class Running(Client):
        def run(self, run_id):
            self.polls += 1
            return {"status": "RUNNING"}
    gateway, client, request = setup_gateway(tmp_path, Running(), max_polls=20)
    now = [0.0]
    gateway.monotonic = lambda: now[0]
    gateway.poll_deadline_seconds = 2
    gateway.pause = lambda seconds: now.__setitem__(0, now[0] + seconds)
    with pytest.raises(ContractError, match="APIFY_POLL_DEADLINE_EXCEEDED"):
        gateway.search(request)
    assert client.starts == 1 and client.polls == 2


def test_normalizer_update_reprocesses_existing_dataset_without_new_paid_run(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    first = gateway.search(request)
    gateway.normalizer_version = "corrected-mapping-v2"
    second = gateway.search(request)
    assert second == first
    assert client.starts == client.polls == 1
    assert len(client.pages) == 2
    assert first[0].observed_at == second[0].observed_at


# --- Trip research: shared categories, routing and start-all-then-poll --------

from tandem_worker.adapters.apify import ApifyResearch, TripResearch  # noqa: E402
from tandem_worker.adapters.trip_actors import StaySearch, TransferSearch  # noqa: E402


class TripClient:
    """Records every call in order; each run succeeds on its second poll."""
    def __init__(self, rows=None):
        self.calls, self.polls = [], {}
        self.rows = rows or {}

    def start(self, actor_id, build, actor_input, max_charge_usd):
        self.calls.append(("start", actor_id))
        return {"id": f"run-{actor_id}-{len(self.calls)}", "defaultDatasetId": f"data-{actor_id}"}

    def run(self, run_id):
        self.calls.append(("poll", run_id))
        self.polls[run_id] = self.polls.get(run_id, 0) + 1
        return {"status": "SUCCEEDED" if self.polls[run_id] >= 2 else "RUNNING", "defaultDatasetId": "data-" + run_id.split("-")[1]}

    def items(self, dataset_id, offset, limit):
        self.calls.append(("items", dataset_id))
        return self.rows.get(dataset_id, [{"price": 100}])[offset:offset + limit] if offset == 0 else []


def flight_normalize(item, search, run):
    return Candidate(id=f"flight-{search.owner}-{run['run_id']}", owner=search.owner, total_cents=item["price"], arrival_epoch=1800010000,
        observed_at=run["observed_at"], valid_until=run["observed_at"] + 60, source_url="https://source.invalid/flight",
        run_ref=run["run_id"], mode="APIFY_OBSERVED", cost_basis="COMPLETE_FLIGHT_TOTAL")


def shared_normalize(category):
    def normalize(item, search, run):
        return [Candidate(id=f"{category.lower()}-{run['run_id']}-{item['price']}", category=category, owner=item.get("owner"),
            total_cents=item["price"], observed_at=run["observed_at"], valid_until=run["observed_at"] + 60,
            source_url=f"https://source.invalid/{category.lower()}", run_ref=run["run_id"], mode="APIFY_OBSERVED",
            cost_basis="OBSERVED_PRICE", missing_critical_fields=("taxes",))]
    return normalize


STAY = StaySearch(destination="Honolulu", check_in=date(2027, 1, 15), check_out=date(2027, 1, 19), adults=2)
TRANSFER = TransferSearch(pickup_location="HNL", dropoff_location="Waikiki", pickup_date=date(2027, 1, 15), pickup_time="14:00", passengers=2)


def shared_input(category):
    def build(search, request):
        if request["purpose"] == "TARGETED" and request.get("blocker_code") not in {f"{category}_UNAVAILABLE", "HARD_LIMIT"}:
            raise ContractError("TARGETED_SEARCH_NOT_AUTHORIZED")
        return {"category": category, **search.model_dump(mode="json")}
    return build


def setup_trip(tmp_path, client=None, categories=("STAY", "TRANSFER"), **kwargs):
    clock, client = FakeClock(), client or TripClient()
    ledger = RunLedger(tmp_path / "runs.sqlite")
    common = dict(room_id="r", input_revision=1, permission_revision=1, clock=clock, pause=lambda seconds: None, **kwargs)
    def flight_input(search, request):
        if request["purpose"] == "TARGETED" and request.get("blocker_code") != "HARD_LIMIT":
            raise ContractError("TARGETED_SEARCH_NOT_AUTHORIZED")
        return {"origin": search.origin, "owner": search.owner}
    flights = ApifyFlightResearch(client, ledger, actor_id="flights", build="1.0.0",
        approved_searches=[ApprovedFlightSearch(owner=o, origin="SFO", destination="HNL", departure_date=date(2027, 1, 15)) for o in ("p1", "p2")],
        build_input=None, request_input_builder=flight_input, normalize_item=flight_normalize, **common)
    searches = {"STAY": STAY, "TRANSFER": TRANSFER}
    shared = {c: ApifyResearch(client, ledger, category=c, actor_id=c.lower(), build="1.0.0", approved_searches=[searches[c]],
                               build_input=None, request_input_builder=shared_input(c), normalize_item=shared_normalize(c), **common)
              for c in categories}
    request = {"room_id": "r", "input_revision": 1, "permission_revision": 1, "members": ["p1", "p2"], "target_owners": ["p1", "p2"],
               "purpose": "INITIAL", "scope": "TRIP", "categories": ["FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"]}
    return TripResearch(flights, shared), client, request


def test_trip_starts_every_run_before_polling_any(tmp_path):
    trip, client, request = setup_trip(tmp_path)
    results = trip.search(request)
    first_poll = next(i for i, call in enumerate(client.calls) if call[0] == "poll")
    starts = [i for i, call in enumerate(client.calls) if call[0] == "start"]
    assert len(starts) == 4 and max(starts) < first_poll
    assert [call[1] for call in client.calls if call[0] == "start"] == ["flights", "flights", "stay", "transfer"]
    assert {(c.category, c.owner) for c in results} == {("FLIGHT", "p1"), ("FLIGHT", "p2"), ("STAY", None), ("TRANSFER", None)}
    # Per-run ledger semantics are unchanged: each run polled twice and recorded.
    report = trip.flights.ledger.report()
    assert [r["category"] for r in report] == ["FLIGHT", "FLIGHT", "STAY", "TRANSFER"]
    assert all(r["state"] == "DONE" and r["polls"] == 2 for r in report)
    # A replay reuses every run: nothing new is started or polled.
    calls = len(client.calls)
    assert trip.search(request) == results
    assert all(call[0] != "start" and call[0] != "poll" for call in client.calls[calls:])


def test_unconfigured_categories_are_skipped_and_routing_follows_the_request(tmp_path):
    trip, client, request = setup_trip(tmp_path, categories=("STAY",))
    results = trip.search({**request, "categories": ["FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"]})
    assert {c.category for c in results} == {"FLIGHT", "STAY"}
    (tmp_path / "only-stay").mkdir()
    trip, client, request = setup_trip(tmp_path / "only-stay", categories=("STAY", "TRANSFER"))
    results = trip.search({**request, "categories": ["STAY"]})
    assert [c.category for c in results] == ["STAY"]
    assert [call[1] for call in client.calls if call[0] == "start"] == ["stay"]


def test_flights_only_scope_cannot_reach_shared_categories(tmp_path):
    trip, client, request = setup_trip(tmp_path)
    with pytest.raises(ContractError, match="RESEARCH_SCOPE_MISMATCH"):
        trip.search({**request, "scope": "FLIGHTS_ONLY"})
    assert client.calls == []
    # Requests without categories (the flight-only coordinator) still work.
    legacy = {k: v for k, v in request.items() if k not in {"scope", "categories"}}
    assert {c.category for c in trip.search(legacy)} == {"FLIGHT"}


def test_targeted_request_reruns_only_categories_that_can_act_on_the_blocker(tmp_path):
    trip, client, request = setup_trip(tmp_path)
    targeted = {**request, "purpose": "TARGETED", "blocker_code": "STAY_UNAVAILABLE"}
    results = trip.search(targeted)
    assert {c.category for c in results} == {"STAY"}
    assert [call[1] for call in client.calls if call[0] == "start"] == ["stay"]
    # HARD_LIMIT: cheaper flights for the targeted owner, and cheaper stays/transfers too.
    hard = {**request, "purpose": "TARGETED", "blocker_code": "HARD_LIMIT", "target_owners": ["p1"]}
    assert {(c.category, c.owner) for c in trip.search(hard)} == {("FLIGHT", "p1"), ("STAY", None), ("TRANSFER", None)}
    with pytest.raises(ContractError, match="TARGETED_SEARCH_NOT_AUTHORIZED"):
        trip.search({**request, "purpose": "TARGETED", "blocker_code": "BAGGAGE_UNKNOWN", "categories": ["STAY", "TRANSFER"]})


def test_shared_candidate_with_an_owner_or_wrong_category_is_quarantined(tmp_path):
    rows = {"data-stay": [{"price": 100}, {"price": 200, "owner": "p1"}]}
    trip, client, request = setup_trip(tmp_path, TripClient(rows), categories=("STAY", "TRANSFER"))
    trip.shared["TRANSFER"].normalize_item = shared_normalize("STAY")  # a mapper bug: stays from the taxi Actor
    results = trip.search(request)
    assert [c.total_cents for c in results if c.category == "STAY"] == [100]
    assert not [c for c in results if c.category == "TRANSFER"]
    report = {r["category"]: r for r in trip.flights.ledger.report()}
    assert report["STAY"]["quarantined"] == 1 and report["TRANSFER"]["quarantined"] == 1


def test_flight_from_a_shared_gateway_is_quarantined(tmp_path):
    trip, client, request = setup_trip(tmp_path, categories=("STAY",))
    trip.shared["STAY"].normalize_item = lambda item, search, run: flight_normalize(item, ApprovedFlightSearch(
        owner="p1", origin="SFO", destination="HNL", departure_date=date(2027, 1, 15)), run)
    assert [c for c in trip.search({**request, "categories": ["STAY"]})] == []


def test_ambiguous_start_anywhere_blocks_new_paid_starts(tmp_path):
    class AmbiguousTransfer(TripClient):
        def start(self, actor_id, *args):
            if actor_id == "transfer":
                self.calls.append(("start", actor_id))
                raise SystemExit()
            return super().start(actor_id, *args)
    trip, client, request = setup_trip(tmp_path, AmbiguousTransfer())
    with pytest.raises(SystemExit):
        trip.search(request)
    starts = len([c for c in client.calls if c[0] == "start"])
    with pytest.raises(ContractError, match="APIFY_START_AMBIGUOUS"):
        trip.search(request)
    assert len([c for c in client.calls if c[0] == "start"]) == starts


def test_one_deadline_covers_all_runs(tmp_path):
    class Running(TripClient):
        def run(self, run_id):
            self.calls.append(("poll", run_id))
            return {"status": "RUNNING"}
    trip, client, request = setup_trip(tmp_path, Running(), max_polls=20)
    now = [0.0]
    trip.monotonic = lambda: now[0]
    trip.poll_deadline_seconds = 3
    trip.pause = lambda seconds: now.__setitem__(0, now[0] + seconds)
    with pytest.raises(ContractError, match="APIFY_POLL_DEADLINE_EXCEEDED"):
        trip.search(request)
    # Two rounds of polling across all four runs (pauses of 1s and 2s), no extra starts.
    assert len([c for c in client.calls if c[0] == "start"]) == 4
    assert len([c for c in client.calls if c[0] == "poll"]) == 8


def test_budget_is_shared_and_blocks_without_resubmitting(tmp_path):
    from tandem_worker.budget import ProviderBudget
    budget = ProviderBudget(tmp_path / "budget.sqlite", "scope", actor_runs=3)
    trip, client, request = setup_trip(tmp_path, budget=budget)
    with pytest.raises(ContractError, match="ACTOR_RUN_BUDGET_EXHAUSTED"):
        trip.search(request)
    assert len([c for c in client.calls if c[0] == "start"]) == 3
    with pytest.raises(ContractError, match="ACTOR_RUN_BUDGET_EXHAUSTED"):
        trip.search(request)
    assert len([c for c in client.calls if c[0] == "start"]) == 3


def test_shared_search_rejects_an_owner_and_flight_search_requires_one(tmp_path):
    ledger = RunLedger(tmp_path / "runs.sqlite")
    common = dict(actor_id="a", build="1.0.0", room_id="r", input_revision=1, permission_revision=1, clock=FakeClock(),
                  build_input=lambda s, p: {}, normalize_item=lambda *a: None)
    with pytest.raises(ValueError):
        ApifyResearch(Client(), ledger, category="STAY",
                      approved_searches=[ApprovedFlightSearch(owner="p1", origin="SFO", destination="LAX", departure_date=date(2027, 1, 1))], **common)
    with pytest.raises(ValueError):
        ApifyFlightResearch(Client(), ledger, approved_searches=[STAY], **common)
    with pytest.raises(ValueError):
        TripResearch(ApifyFlightResearch(Client(), ledger, approved_searches=[], **common),
                     {"TRANSFER": ApifyResearch(Client(), ledger, category="STAY", approved_searches=[STAY], **common)})


def test_live_providers_build_trip_research_and_preflight_every_actor(live_case):
    import json as _json
    from tandem_worker.adapters.trip_actors import ACTORS
    from tandem_worker.config import LiveConfig
    from tandem_worker.contracts import ResearchRequest
    from tandem_worker.live import check_providers, make_providers
    data = _json.loads(live_case.config.model_dump_json())
    data["limits"]["actor_runs"] = 3
    data["trip"] = {"stay": {"destination": "Los Angeles", "check_in": "2027-01-15", "check_out": "2027-01-17", "adults": 1},
                    "dinner": {"query": "restaurant for groups", "location": "Los Angeles, CA"}}
    config = LiveConfig.model_validate(data)
    providers = make_providers(config, live_case.settings, live_case.directory, clock=live_case.clock,
                               claude_client=live_case.claude, apify_client=live_case.apify)
    assert providers.components == ("FLIGHT", "STAY")
    with pytest.raises(ContractError, match="LIVE_PREFLIGHT_REQUIRED"):
        providers.research.search({})
    checked = providers.preflight()
    assert set(checked["trip"]) == {"STAY", "DINNER"}
    assert [p[0] for p in live_case.apify.preflights][1:] == [ACTORS["STAY"].actor_id, ACTORS["DINNER"].actor_id]
    request = ResearchRequest(purpose="INITIAL", members=("p1",), target_owners=("p1",), scope="TRIP", room_id=config.room_id,
        input_revision=1, permission_revision=1, observed_at=live_case.clock.now(),
        categories=("FLIGHT", "STAY", "TRANSFER", "ACTIVITY", "DINNER"))
    results = providers.research.search(request.model_dump(mode="json"))
    # The fake returns flight rows from every dataset: the stay and dinner runs quarantine them.
    assert {c.category for c in results} == {"FLIGHT"}
    assert [s[0] for s in live_case.apify.starts][1:] == [ACTORS["STAY"].actor_id, ACTORS["DINNER"].actor_id]
    report = providers.report()
    assert report["components"] == ["FLIGHT", "STAY"]
    assert [r["quarantined"] for r in report["research_runs"] if r["category"] != "FLIGHT"] == [1, 1]
    offline = check_providers(config, live_case.settings, online=True, clock=live_case.clock,
                              claude_client=live_case.claude, apify_client=live_case.apify)
    assert set(offline["trip_actors"]) == set(offline["access"]["trip"]) == {"STAY", "DINNER"}
