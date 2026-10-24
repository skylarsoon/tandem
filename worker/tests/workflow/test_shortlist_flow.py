"""The coordinator shortlists between research and the solver: the solver sees
at most `shortlist_size` flights per traveller from everything found so far,
a traveller with nothing usable is reported rather than planned around, and
an infeasible shortlist is retried with untried flights before concluding."""
from collections import Counter

from tandem_worker.backend import LocalBackend
from tandem_worker.contracts import Candidate, Commitment, Conflict, Limits, SolverResult
from tandem_worker.fixtures import ScriptedModel, scenario_inputs
from tandem_worker.ports import FakeClock
from tandem_worker.runner import Worker
from tandem_worker.solver import POLICY, TripSolver

HOUR = 3600


class ManyFlights:
    """Twenty flights per traveller on every search, new observation ids each time."""

    def __init__(self, per_owner=20):
        self.per_owner, self.requests = per_owner, []

    def search(self, request):
        self.requests.append(request)
        now, tag = request["observed_at"], f"{request['purpose'].lower()}{len(self.requests)}"
        return [Candidate(id=f"{o}-{tag}-{i:02d}", owner=o, total_cents=30_000 + i * 500,
                          arrival_epoch=1_800_052_000 + i * HOUR // 2, arrival_timezone="Pacific/Honolulu",
                          observed_at=now, valid_until=now + 3600, source_url=f"https://fixtures.invalid/{o}/{i}",
                          run_ref="fixture", mode="FIXTURE", cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=True,
                          flight_identity=f"{o}-{i}")
                for o in request["target_owners"] for i in range(self.per_owner)]


class SpySolver:
    """Records what the solver is shown; optionally refuses the first shortlist."""

    def __init__(self, refuse_first=False):
        self.inner, self.calls, self.refuse_first = TripSolver(), [], refuse_first

    def solve(self, snapshot, briefs, candidates):
        self.calls.append(sorted(c.id for c in candidates if c.category == "FLIGHT"))
        if self.refuse_first and len(self.calls) == 1:
            return SolverResult(status="INFEASIBLE", policy_version=POLICY,
                                conflicts=(Conflict(code="ARRIVAL_WINDOW", closest_spread_seconds=9000, required_seconds=7200),))
        return self.inner.solve(snapshot, briefs, candidates)


def per_owner(ids):
    return Counter(i.split("-")[0] for i in ids)


def setup(tmp_path, *, solver, briefs=None, commitments=(), limits=None):
    clock, limits = FakeClock(), limits or Limits()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock, limits)
    snapshot, defaults = scenario_inputs("happy")
    job = backend.seed(snapshot.model_copy(update={"commitments": tuple(commitments)}), briefs or defaults)
    research = ManyFlights()
    worker = Worker(backend, ScriptedModel("happy"), research, tmp_path / "checkpoints.sqlite", clock=clock,
                    limits=limits, solver=solver)
    return backend, worker, job, research


def test_the_solver_never_sees_more_than_five_flights_per_traveller(tmp_path):
    solver = SpySolver()
    backend, worker, job, research = setup(tmp_path, solver=solver)
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert len(research.requests) == 1 and solver.calls
    for shown in solver.calls:
        assert per_owner(shown) == {o: 5 for o in ("p1", "p2", "p3", "p4")}


def test_the_size_is_configurable(tmp_path):
    solver = SpySolver()
    _, worker, job, _ = setup(tmp_path, solver=solver, limits=Limits(shortlist_size=3))
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert per_owner(solver.calls[0]) == {o: 3 for o in ("p1", "p2", "p3", "p4")}


def test_a_traveller_with_no_usable_flight_is_reported_not_planned_around(tmp_path):
    _, briefs = scenario_inputs("happy")
    briefs[1] = briefs[1].model_copy(update={"hard_cap_cents": 10_000})  # every fare is above p2's limit
    solver = SpySolver()
    backend, worker, job, research = setup(tmp_path, solver=solver, briefs=briefs)
    assert worker.run_one(job)["outcome"] == "NO_FEASIBLE_CANDIDATE"
    assert solver.calls == [], "no plan is solved as if p2 had a flight"
    assert len(research.requests) > 1, "it searched again for p2 before concluding"
    assert all(r["target_owners"] == ["p2"] for r in research.requests[1:])
    assert any("30" in note for note in backend.owner_view("p2")["notes"]), "p2 is told the cheapest fare found"
    event = next(e for e in backend.shared_report()["events"] if e["kind"] == "NO_FEASIBLE_PLAN")
    assert event["payload"]["flights_found"] == 60 and event["payload"]["flights_checked"] == 15


def test_an_infeasible_shortlist_is_retried_with_untried_flights_before_searching_again(tmp_path):
    solver = SpySolver(refuse_first=True)
    _, worker, job, research = setup(tmp_path, solver=solver)
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert len(research.requests) == 1, "no new search was needed"
    first, second = solver.calls[:2]
    assert per_owner(second) == {o: 5 for o in ("p1", "p2", "p3", "p4")}
    assert set(second) - set(first), "the second pass showed flights the first didn't"


def test_an_agreed_flight_that_cannot_be_kept_is_never_dropped_silently(tmp_path):
    solver = SpySolver()
    gone = Commitment(decision_id="flights", scope="FLIGHT_BUNDLE", refs=("p1-agreed-earlier",))
    _, worker, job, _ = setup(tmp_path, solver=solver, commitments=[gone])
    assert worker.run_one(job)["outcome"] == "LOCKED_CANDIDATE_UNAVAILABLE"
    assert solver.calls == []
