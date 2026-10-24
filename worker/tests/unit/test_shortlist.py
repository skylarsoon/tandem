"""The shortlist between research and the solver: at most N distinct, valid
flights per traveller, deterministic, never altering or inventing candidates."""
import random

from tandem_worker.contracts import Brief, Candidate, Preferences, Snapshot
from tandem_worker.shortlist import closest_window, shortlist_flights
from tandem_worker.solver import TripSolver

HOUR = 3600
T0 = 1_800_000_000
NOW = 1000.0


def flight(owner, cid, price, arrive_hours, bag=True, **facts):
    return Candidate(id=cid, owner=owner, total_cents=price, arrival_epoch=T0 + int(arrive_hours * HOUR), observed_at=0,
                     valid_until=10**10, source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis="COMPLETE_FLIGHT_TOTAL", baggage_included=bag, **facts)


def brief(owner, cap=100_000, **limits):
    return Brief(owner=owner, hard_cap_cents=cap, preferences=Preferences(confirmed=True), **limits)


def many(owner, count=20):
    """`count` flights, cheaper earlier, spread over the day."""
    return [flight(owner, f"{owner}-{i:02d}", 20_000 + i * 1_000, 6 + i * 0.75) for i in range(count)]


def run(owners, candidates, briefs=None, limit=5, **kw):
    snapshot = Snapshot(room_id="r", members=tuple(owners))
    return shortlist_flights(candidates, snapshot, briefs or [brief(o) for o in owners], limit=limit, now=NOW, **kw)


def per_owner(result):
    counts = {}
    for c in result.candidates:
        if c.category == "FLIGHT":
            counts[c.owner] = counts.get(c.owner, 0) + 1
    return counts


def test_four_travellers_with_twenty_flights_each_get_at_most_five():
    result = run("abcd", [c for o in "abcd" for c in many(o)])
    assert per_owner(result) == {o: 5 for o in "abcd"}
    assert [(g.group, g.found, g.valid, len(g.kept)) for g in result.groups] == [(o, 20, 20, 5) for o in "abcd"]
    assert result.truncated and not result.empty_groups


def test_five_travellers_still_get_at_most_five_each():
    assert per_owner(run("abcde", [c for o in "abcde" for c in many(o)])) == {o: 5 for o in "abcde"}


def test_fewer_valid_flights_than_the_limit_are_all_kept():
    result = run("ab", many("a") + many("b", 3))
    assert sorted(c.id for c in result.candidates if c.owner == "b") == ["b-00", "b-01", "b-02"]
    assert (result.groups[1].valid, result.groups[1].kept) == (3, ("b-00", "b-01", "b-02"))


def test_a_traveller_without_any_valid_flight_is_reported_not_hidden():
    result = run("ab", many("a") + many("b"), briefs=[brief("a"), brief("b", cap=10_000)])
    assert result.empty_groups == ("b",)
    assert result.groups[1].excluded == {"OVER_FLIGHT_LIMIT": 20}
    # The solver's own explanation still sees why (from the full pool).
    conflict = TripSolver.owner_conflict("b", brief("b", cap=10_000), many("b"))
    assert (conflict.code, conflict.cheapest_cents) == ("HARD_LIMIT", 20_000)


def test_a_cheapest_fare_that_breaks_a_hard_limit_is_excluded():
    no_bag = flight("a", "a-cheap-no-bag", 9_000, 9, bag=False)
    connecting = flight("a", "a-cheap-two-stops", 9_500, 9, stops=2, arrival_local="12:00")
    late = flight("a", "a-cheap-late", 9_800, 23, arrival_local="23:30")
    pool = [no_bag, connecting, late] + [flight("a", f"a-{i}", 20_000 + i, 10 + i, stops=0, arrival_local="12:00")
                                         for i in range(8)]
    result = run("a", pool, briefs=[brief("a", baggage_required=True, max_stops=0,
                                          arrival_window={"earliest": "07:00", "latest": "22:00"})])
    kept = {c.id for c in result.candidates}
    assert not kept & {no_bag.id, connecting.id, late.id}
    assert result.groups[0].excluded == {"ARRIVAL_TIME": 1, "NO_BAG": 1, "TOO_MANY_STOPS": 1}
    assert "a-0" in kept, "the cheapest valid fare takes the cheapest slot"


def test_meaningfully_different_arrivals_beat_five_near_identical_cheap_ones():
    clones = [flight("a", f"a-am-{i}", 10_000 + i * 10, 9 + i / 12) for i in range(6)]  # 9:00-9:25, cheapest
    spread = [flight("a", "a-noon", 14_000, 12), flight("a", "a-pm", 15_000, 16), flight("a", "a-eve", 16_000, 20)]
    result = run("a", clones + spread)
    hours = sorted({(c.arrival_epoch - T0) // HOUR for c in result.candidates})
    assert {"a-noon", "a-pm", "a-eve"} <= {c.id for c in result.candidates}
    assert len(hours) >= 4, f"distinct arrival times kept, got {hours}"


def test_a_locked_flight_is_always_kept_or_reported():
    pool = many("a")
    result = run("a", pool, locked=["a-12"])
    assert "a-12" in {c.id for c in result.candidates} and len(result.candidates) == 5
    assert run("a", pool, locked=["a-gone"]).lock_conflicts == ("a-gone",)
    expired = flight("a", "a-expired", 1, 9).model_copy(update={"valid_until": NOW - 1})
    assert run("a", pool + [expired], locked=["a-expired"]).lock_conflicts == ("a-expired",)


def test_identical_input_in_any_order_gives_the_identical_shortlist():
    pool = [c for o in "abcd" for c in many(o)]
    first = run("abcd", pool)
    for seed in range(5):
        shuffled = list(pool)
        random.Random(seed).shuffle(shuffled)
        assert run("abcd", shuffled) == first


def test_the_limit_holds_after_cached_and_new_results_are_merged():
    cached = [c.model_copy(update={"flight_identity": c.id}) for c in many("a")]
    # A new search returns the same flights again (newer observations) plus new ones.
    again = [c.model_copy(update={"id": c.id + "-new", "observed_at": 5}) for c in cached[:10]]
    new = [flight("a", f"a-new-{i}", 30_000 + i, 7 + i) for i in range(10)]
    result = run("a", cached + again + new)
    assert per_owner(result) == {"a": 5}
    assert result.groups[0].excluded == {"DUPLICATE": 10} and result.groups[0].valid == 30


def test_candidates_come_through_unchanged_and_the_input_is_untouched():
    pool = many("a") + [Candidate(id="stay-1", category="STAY", total_cents=50_000, observed_at=0, valid_until=10**10,
                                  source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                                  cost_basis="COMPLETE_STAY_TOTAL", capacity=4)]
    before = [c.model_dump() for c in pool]
    result = run("a", pool)
    originals = {c.id: c for c in pool}
    assert all(c is originals[c.id] for c in result.candidates), "the same records, not copies or edits"
    assert "stay-1" in {c.id for c in result.candidates}, "other categories pass through"
    assert [c.model_dump() for c in pool] == before


def test_the_only_combination_landing_together_survives_the_shortlist():
    # a's cheap flights land in the morning, b's in the evening; they only meet at noon,
    # on each one's 9th-cheapest fare.
    a = [flight("a", f"a-{i}", 10_000 + i * 100, 6 + i * 0.25) for i in range(8)] + [flight("a", "a-noon", 19_000, 12)]
    b = [flight("b", f"b-{i}", 10_000 + i * 100, 18 + i * 0.25) for i in range(8)] + [flight("b", "b-noon", 19_000, 12.5)]
    assert closest_window({"a": a, "b": b}) == (T0 + 12 * HOUR, T0 + int(12.5 * HOUR))
    result = run("ab", a + b)
    assert {"a-noon", "b-noon"} <= {c.id for c in result.candidates}
    snapshot = Snapshot(room_id="r", members=("a", "b"), arrival_overlap_seconds=HOUR)
    plan = TripSolver().solve(snapshot, [brief("a"), brief("b")], list(result.candidates))
    assert plan.status == "OPTIMAL" and set(plan.selected) == {"a-noon", "b-noon"}


def test_a_second_pass_rotates_in_untried_flights_but_keeps_the_anchors():
    first = run("a", many("a"))
    second = run("a", many("a"), tried=first.groups[0].kept)
    kept1, kept2 = set(first.groups[0].kept), set(second.groups[0].kept)
    assert "a-00" in kept1 & kept2, "the cheapest stays"
    assert len(kept2 - kept1) >= 3, "diversity places go to flights not tried yet"
    assert second.groups[0].untried_valid == 20 - len(kept1 | kept2)
