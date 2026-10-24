"""minimax_v1: hard limits first, then the smallest worst burden, then cost;
with explanations for chosen plans and for why no plan exists."""
from tandem_worker.contracts import Brief, Candidate, Preferences, Snapshot
from tandem_worker.solver import FlightSolver, closest_spread

HOUR = 3600
T0 = 1_800_000_000


def flight(owner, cid, price, arrive_hours, bag=True):
    return Candidate(id=cid, owner=owner, total_cents=price, arrival_epoch=T0 + int(arrive_hours * HOUR), observed_at=0,
                     valid_until=10**10, source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=bag)


def brief(owner, cap, bag=False):
    return Brief(owner=owner, hard_cap_cents=cap, baggage_required=bag, preferences=Preferences(confirmed=True))


def solve(members, briefs, candidates, window=HOUR):
    snapshot = Snapshot(room_id="r", members=tuple(members), arrival_overlap_seconds=window)
    return FlightSolver().solve(snapshot, briefs, candidates)


def test_minimax_prefers_a_shared_compromise_over_dumping_it_on_one_person():
    # Morning plan: a and b fly cheap, c pays $90 extra (total burden 90, worst 90).
    # Afternoon plan: a and b pay $50 extra each, c flies cheap (total 100, worst 50).
    candidates = [flight("a", "a-am", 10000, 9), flight("a", "a-pm", 15000, 15),
                  flight("b", "b-am", 10000, 9), flight("b", "b-pm", 15000, 15),
                  flight("c", "c-am", 19000, 9), flight("c", "c-pm", 10000, 15)]
    result = solve("abc", [brief(o, 50000) for o in "abc"], candidates)
    assert result.policy_version == "minimax_v1" and result.status == "OPTIMAL"
    assert result.selected == ("a-pm", "b-pm", "c-pm"), "the cheaper morning plan puts the whole burden on c"
    assert result.group.max_extra_cents == 5000 and result.group.travellers_paying_extra == 2
    assert result.group.total_cents == 40000


def test_cost_breaks_ties_between_equally_fair_plans():
    candidates = [flight("a", "a1", 10000, 9), flight("a", "a2", 12000, 9.5), flight("b", "b1", 10000, 9)]
    assert solve("ab", [brief("a", 50000), brief("b", 50000)], candidates).selected == ("a1", "b1")


def test_impacts_explain_who_pays_more_and_why():
    # Dave's cheapest flight lands 4h after everyone else; his on-time flight costs $125 more.
    candidates = [flight("alice", "al", 29200, 21.1), flight("carol", "ca", 43200, 21.2),
                  flight("dave", "dave-late", 50400, 23.9), flight("dave", "dave-on-time", 62900, 21.9)]
    briefs = [brief("alice", 30000), brief("carol", 45000), brief("dave", 65000)]
    result = solve(["alice", "carol", "dave"], briefs, candidates, window=2 * HOUR)
    impacts = {i.owner: i for i in result.impacts}
    assert impacts["dave"].candidate == "dave-on-time"
    assert (impacts["dave"].extra_cents, impacts["dave"].reason) == (12500, "ARRIVAL_WINDOW")
    assert impacts["alice"].extra_cents == 0 and impacts["alice"].reason == "NONE"
    group = result.group
    assert group.travellers_paying_extra == 1 and group.max_extra_cents == 12500
    assert group.cheapest_total_cents == 29200 + 43200 + 50400, "what the group would pay ignoring the window"
    assert group.cheapest_spread_seconds == 10080  # 2h48m: Dave would land long after the others
    assert group.latest_arrival - group.earliest_arrival <= 2 * HOUR


def test_budget_conflicts_say_which_limit_blocks_and_the_cheapest_fare():
    candidates = [flight("alice", "al", 29200, 21), flight("carol", "ca", 43200, 21), flight("dave", "d", 50000, 21)]
    briefs = [brief("alice", 25000), brief("carol", 35000), brief("dave", 60000)]
    result = solve(["alice", "carol", "dave"], briefs, candidates)
    assert result.status == "INFEASIBLE" and result.selected == ()
    assert {(c.owner, c.code, c.cheapest_cents) for c in result.conflicts} == {
        ("alice", "HARD_LIMIT", 29200), ("carol", "HARD_LIMIT", 43200)}


def test_missing_bag_option_is_its_own_conflict():
    candidates = [flight("bob", "b1", 20000, 9, bag=False), flight("bob", "b2", 26000, 9, bag=False)]
    result = solve(["bob"], [brief("bob", 50000, bag=True)], candidates)
    assert [(c.code, c.owner) for c in result.conflicts] == [("BAGGAGE_UNAVAILABLE", "bob")]
    priced_bag = [flight("bob", "b3", 60000, 9, bag=True)]
    result = solve(["bob"], [brief("bob", 50000, bag=True)], candidates + priced_bag)
    assert [(c.code, c.cheapest_cents) for c in result.conflicts] == [("HARD_LIMIT", 60000)]


def test_unknown_baggage_stays_selectable_for_the_agent_to_question():
    result = solve(["bob"], [brief("bob", 50000, bag=True)], [flight("bob", "b1", 20000, 9, bag=None)])
    assert result.selected == ("b1",)


def test_arrival_conflict_reports_the_closest_possible_spread():
    candidates = [flight("a", "a1", 10000, 8), flight("a", "a2", 10000, 12),
                  flight("b", "b1", 10000, 15), flight("b", "b2", 10000, 18)]
    result = solve("ab", [brief("a", 50000), brief("b", 50000)], candidates, window=2 * HOUR)
    [conflict] = result.conflicts
    assert (conflict.code, conflict.owner) == ("ARRIVAL_WINDOW", None)
    assert conflict.closest_spread_seconds == 3 * HOUR and conflict.required_seconds == 2 * HOUR


def test_closest_spread_needs_one_option_per_traveller():
    options = {"a": [flight("a", "a1", 1, 1), flight("a", "a2", 1, 10)], "b": [flight("b", "b1", 1, 4)],
               "c": [flight("c", "c1", 1, 9)]}
    assert closest_spread(options) == 6 * HOUR
