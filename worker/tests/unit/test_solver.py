"""minimax_v1: hard limits first, then the smallest worst burden, then cost;
with explanations for chosen plans and for why no plan exists."""
import pytest

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


# ---------- whole trips: flights plus a shared stay and transfer ----------

def stay(cid, price, guests=4, bedrooms=2, rating=4.8):
    return Candidate(id=cid, category="STAY", total_cents=price, observed_at=0, valid_until=10**10,
                     source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE", cost_basis="COMPLETE_STAY_TOTAL",
                     capacity=guests, bedrooms=bedrooms, rating=rating)


def transfer(cid, price, seats=4):
    return Candidate(id=cid, category="TRANSFER", total_cents=price, observed_at=0, valid_until=10**10,
                     source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis="COMPLETE_TRANSFER_TOTAL", capacity=seats)


def trip(briefs, candidates, window=2 * HOUR):
    snapshot = Snapshot(room_id="r", members=tuple(b.owner for b in briefs), arrival_overlap_seconds=window,
                        components=("FLIGHT", "STAY", "TRANSFER"))
    return FlightSolver().solve(snapshot, briefs, candidates)


FLIGHTS = [flight(o, f"{o}-f", 30000, 9) for o in "abcd"]


def test_trip_picks_one_stay_and_transfer_and_splits_them_equally():
    result = trip([brief(o, 50000) for o in "abcd"],
                  FLIGHTS + [stay("big", 160000), stay("small", 80000, guests=2, bedrooms=1), transfer("van", 12000),
                             transfer("sedan", 5000, seats=3)])
    assert result.selected == ("a-f", "b-f", "c-f", "d-f", "big", "van"), "the small stay and sedan don't fit four"
    assert result.group.share_each_cents == 43000 and result.group.total_cents == 4 * 30000 + 172000
    assert {(i.flight_cents, i.share_cents, i.price_cents) for i in result.impacts} == {(30000, 43000, 73000)}


def test_own_bedroom_requirements_raise_the_stay_and_say_by_how_much():
    briefs = [brief(o, 50000).model_copy(update={"own_bedroom": o in "ab"}) for o in "abcd"]
    stays = [stay("two-bed", 100000, bedrooms=2), stay("three-bed", 136000, bedrooms=3)]
    result = trip(briefs, FLIGHTS + stays + [transfer("van", 8000)])
    assert result.group.stay == "three-bed"  # a and b alone, c and d together
    assert result.group.stay_premium_cents == 36000


def test_trip_limit_names_the_cheapest_trip_privately():
    briefs = [brief(o, 50000).model_copy(update={"trip_cap_cents": 60000 if o == "a" else None}) for o in "abcd"]
    result = trip(briefs, FLIGHTS + [stay("s", 160000), transfer("van", 12000)])
    assert [(c.code, c.owner, c.cheapest_cents, c.trip_cap_cents) for c in result.conflicts] == [("TRIP_LIMIT", "a", 73000, 60000)]


def test_a_tight_trip_limit_steers_the_shared_choice_for_everyone():
    # a can't afford the nicer stay; the plan uses the one everyone can afford.
    briefs = [brief(o, 50000).model_copy(update={"trip_cap_cents": 70000 if o == "a" else None,
                                                  "stay_min_rating": 4.0 if o == "b" else None}) for o in "abcd"]
    stays = [stay("nice", 180000, rating=4.9), stay("fine", 150000, rating=4.2), stay("grim", 90000, rating=3.1)]
    result = trip(briefs, FLIGHTS + stays + [transfer("van", 8000)])
    assert result.group.stay == "fine", "grim misses b's rating; nice breaks a's trip limit"


@pytest.mark.parametrize("stays,requirement", [
    ([], None),
    ([stay("tiny", 50000, guests=2)], "CAPACITY"),
    ([stay("one-bed", 50000, bedrooms=1)], "BEDROOMS"),
    ([stay("low", 50000, rating=3.0)], "RATING"),
])
def test_stay_conflicts_name_the_requirement_not_the_person(stays, requirement):
    briefs = [brief(o, 50000).model_copy(update={"stay_min_rating": 4.5 if o == "c" else None}) for o in "abcd"]
    result = trip(briefs, FLIGHTS + stays + [transfer("van", 8000)])
    [conflict] = result.conflicts
    assert (conflict.code, conflict.requirement, conflict.owner) == ("STAY_UNAVAILABLE", requirement, None)


def test_trip_validator_checks_shared_items_and_trip_limits():
    from tandem_worker.solver import TripValidator
    briefs = [brief(o, 50000).model_copy(update={"trip_cap_cents": 70000}) for o in "abcd"]
    snapshot = Snapshot(room_id="r", members=tuple("abcd"), components=("FLIGHT", "STAY", "TRANSFER"))
    candidates = FLIGHTS + [stay("s", 160000), transfer("van", 12000), transfer("sedan", 5000, seats=3)]
    flights = tuple(f.id for f in FLIGHTS)
    ok = TripValidator().validate(snapshot, briefs, candidates, flights + ("s", "van"), 1)
    assert ok.checks == ("TRIP_COST",), "$300 + $430 share is above $700"
    loose = [b.model_copy(update={"trip_cap_cents": None}) for b in briefs]
    assert TripValidator().validate(snapshot, loose, candidates, flights + ("s", "van"), 1).valid
    assert TripValidator().validate(snapshot, loose, candidates, flights + ("s", "sedan"), 1).checks == ("TRANSFER_CAPACITY",)
    assert TripValidator().validate(snapshot, loose, candidates, flights + ("s",), 1).checks == ("INVALID_COVERAGE",)


def test_people_per_bedroom_is_a_room_setting():
    briefs = [brief(o, 50000) for o in "abcd"]
    stays = [stay("one-bed", 60000, bedrooms=1), stay("two-bed", 90000, bedrooms=2)]
    snapshot = Snapshot(room_id="r", members=tuple("abcd"), components=("FLIGHT", "STAY", "TRANSFER"))
    assert FlightSolver().solve(snapshot, briefs, FLIGHTS + stays + [transfer("van", 8000)]).group.stay == "two-bed"
    dorm = snapshot.model_copy(update={"people_per_bedroom": 4})
    assert FlightSolver().solve(dorm, briefs, FLIGHTS + stays + [transfer("van", 8000)]).group.stay == "one-bed"


def test_room_needs_premium_compares_only_with_approvable_stays():
    briefs = [brief(o, 50000).model_copy(update={"own_bedroom": o == "a"}) for o in "abcd"]
    unpriced = stay("cheap-unconfirmed", 50000, bedrooms=1).model_copy(update={"cost_basis": "OBSERVED_PRICE"})
    result = trip(briefs, FLIGHTS + [unpriced, stay("three-bed", 120000, bedrooms=3), stay("two-bed", 100000, bedrooms=2)]
                  + [transfer("van", 8000)])
    assert result.group.stay == "three-bed" and result.group.stay_premium_cents == 20000, "vs the $1,000 two-bed, not $500"
