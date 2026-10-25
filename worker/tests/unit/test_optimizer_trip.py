"""The multi-selection optimizer planning whole trips through the bridge: flights
plus the stay and transfer, the room's arrival window, whole-trip limits, and
the same explanations as TripSolver when nothing fits."""
import random

from tandem_worker.contracts import Brief, Candidate, Preferences, Snapshot
from tandem_worker.optimizer_bridge import POLICY, TandemOptimizerBridge
from tandem_worker.solver import TripSolver

HOUR = 3600
T0 = 1_800_000_000


def flight(owner, cid, price, arrive_hours, **facts):
    return Candidate(id=cid, owner=owner, total_cents=price, arrival_epoch=T0 + int(arrive_hours * HOUR), observed_at=0,
                     valid_until=10**10, source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=True, **facts)


def shared(category, cid, price, **facts):
    return Candidate(id=cid, category=category, total_cents=price, observed_at=0, valid_until=10**10,
                     source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis=f"COMPLETE_{category}_TOTAL", capacity=4, **facts)


def brief(owner, cap=100_000, **limits):
    return Brief(owner=owner, hard_cap_cents=cap, preferences=Preferences(confirmed=True), **limits)


def room(members="abcd", components=("FLIGHT", "STAY", "TRANSFER"), window=2 * HOUR, history=None):
    return Snapshot(room_id="r", members=tuple(members), components=components, arrival_overlap_seconds=window,
                    compromise_history=history or {})


def trip_pool(members="abcd"):
    flights = [flight(m, f"{m}-{i}", 30_000 + i * 2_000, 9 + i) for m in members for i in range(3)]
    return flights + [shared("STAY", "stay-cheap", 120_000, bedrooms=2), shared("STAY", "stay-nice", 200_000, bedrooms=4),
                      shared("TRANSFER", "van", 12_000)]


def test_plans_a_whole_trip_with_the_cheapest_valid_parts():
    result = TandemOptimizerBridge().solve(room(), [brief(m) for m in "abcd"], trip_pool())
    assert result.status == "OPTIMAL" and result.policy_version == POLICY
    assert result.selected == ("a-0", "b-0", "c-0", "d-0", "stay-cheap", "van")
    assert result.group.total_cents == 4 * 30_000 + 120_000 + 12_000
    assert all(i.share_cents == (120_000 + 12_000) // 4 for i in result.impacts), "an equal share each"


def test_the_arrival_window_rules_out_the_cheapest_flights():
    pool = trip_pool("ab") + [flight("a", "a-dawn", 10_000, 2)]  # cheapest, but lands far from b's options
    result = TandemOptimizerBridge().solve(room("ab"), [brief("a"), brief("b")], pool)
    assert "a-dawn" not in result.selected and result.selected[:2] == ("a-0", "b-0")


def test_a_whole_trip_limit_is_respected_with_the_shared_costs():
    # a: $300 flight + $330 share = $630; a $620 limit forces nothing cheaper, so no plan.
    briefs = [brief("a", trip_cap_cents=62_000)] + [brief(m) for m in "bcd"]
    result = TandemOptimizerBridge().solve(room(), briefs, trip_pool())
    assert result.status == "INFEASIBLE"
    [conflict] = result.conflicts
    assert (conflict.code, conflict.owner, conflict.cheapest_cents) == ("TRIP_LIMIT", "a", 63_000)


def test_onboarding_limits_are_understood_not_rejected():
    briefs = [brief("a", arrival_window={"earliest": "10:00", "latest": "22:00"}, max_stops=0),
              brief("b", origin_airport="LAX", no_budget_limit=True)]
    pool = [flight("a", "a-early", 20_000, 9, arrival_local="09:00", stops=0),
            flight("a", "a-ok", 25_000, 11, arrival_local="11:00", stops=0),
            flight("a", "a-stop", 21_000, 11, arrival_local="11:00", stops=1),
            flight("b", "b-0", 30_000, 11)]
    result = TandemOptimizerBridge().solve(room("ab", components=("FLIGHT",)), briefs, pool)
    assert result.status == "OPTIMAL" and result.selected == ("a-ok", "b-0")


def test_no_combination_landing_together_is_explained_like_trip_solver():
    pool = [flight("a", "a-0", 20_000, 6), flight("b", "b-0", 20_000, 20)]
    briefs = [brief("a"), brief("b")]
    ours = TandemOptimizerBridge().solve(room("ab", components=("FLIGHT",)), briefs, pool)
    theirs = TripSolver().solve(room("ab", components=("FLIGHT",)), briefs, pool)
    assert ours.status == theirs.status == "INFEASIBLE"
    assert ours.conflicts == theirs.conflicts and ours.conflicts[0].code == "ARRIVAL_WINDOW"


def test_history_reaches_the_optimizer_unchanged():
    bridge = TandemOptimizerBridge()
    bridge.solve(room("ab", components=("FLIGHT",), history={"a": 0.6}), [brief("a"), brief("b")], trip_pool("ab"))
    assert bridge.last_request["compromise_history"] == {"a": 0.6, "b": 0.0}


def test_agrees_with_trip_solver_on_feasibility_and_never_costs_more():
    """Cost-only ranking: same feasible/infeasible verdicts as TripSolver, and a total
    never above TripSolver's (which trades cost for a smaller worst burden)."""
    for seed in range(25):
        rng = random.Random(seed)
        members = "abc"
        pool = [flight(m, f"{m}-{i}", rng.randrange(20_000, 60_000, 500), rng.uniform(6, 22)) for m in members for i in range(4)]
        pool += [shared("STAY", f"stay-{i}", rng.randrange(60_000, 200_000, 1_000)) for i in range(3)]
        briefs = [brief(m, trip_cap_cents=rng.choice([None, 70_000, 90_000])) for m in members]
        snapshot = room(members, components=("FLIGHT", "STAY"), window=rng.choice([HOUR, 3 * HOUR, 8 * HOUR]))
        ours, theirs = TandemOptimizerBridge().solve(snapshot, briefs, pool), TripSolver().solve(snapshot, briefs, pool)
        assert (ours.status == "INFEASIBLE") == (theirs.status == "INFEASIBLE"), seed
        if ours.status != "INFEASIBLE":
            assert ours.group.total_cents <= theirs.group.total_cents, seed
