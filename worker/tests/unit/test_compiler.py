"""The generic compiler: decisions, rules and needs as data become the optimizer's
request, with no category-specific code."""
import inspect

from tandem_worker import compiler
from tandem_worker.compiler import compile_request
from tandem_worker.contracts import Candidate, Snapshot
from tandem_worker.rules import Need, RoomRules, Rule

HOUR, T0 = 3600, 1_800_000_000


def offer(category, cid, price, owner=None, **facts):
    basis = {"FLIGHT": "COMPLETE_FLIGHT_TOTAL", "STAY": "COMPLETE_STAY_TOTAL", "TRANSFER": "COMPLETE_TRANSFER_TOTAL"}
    return Candidate(id=cid, category=category, owner=owner, total_cents=price, observed_at=0, valid_until=10**10,
                     source_url="https://fixtures.invalid", run_ref="r", mode="FIXTURE",
                     cost_basis=basis.get(category, "ESTIMATE_PER_PERSON"), **facts)


def test_no_category_is_named_in_the_code():
    source = inspect.getsource(compiler)
    for word in ("FLIGHT", "STAY", "TRANSFER", "DINNER", "ACTIVITY", "bedroom", "stops", "arrival"):
        assert word not in source.split('"""', 2)[2], f"{word} is knowledge that belongs in the catalogs"


def test_a_new_kind_of_decision_is_only_data():
    """A group dinner: one for the group, paid per person, filtered by someone's rule."""
    room = Snapshot(room_id="r", members=("a", "b"), components=("DINNER",))
    rules = RoomRules(rules=(Rule(owner="b", category="DINNER", attribute="rating", operator="gte", value=4.0),))
    pool = [offer("DINNER", "diner", 2000, rating=3.5), offer("DINNER", "bistro", 4500, rating=4.6)]
    compiled = compile_request(room, rules, pool, {})
    [group] = compiled.groups
    assert (group.id, [c.id for c in group.options], group.excluded) == ("dinner", ["bistro"], {"rating": 1})
    assert compiled.request["candidates"] == [{"id": "bistro", "attributes": {"total_cents": 4500, "rating": 4.6,
                                                                              "price_cents": 4500, "person_cost": 9000}}]


def test_shared_picks_hold_everyones_needs_and_fit_everyone():
    room = Snapshot(room_id="r", members=("a", "b", "c", "d"), components=("STAY",), people_per_bedroom=2)
    pool = [offer("STAY", "small", 100_000, capacity=4, bedrooms=2), offer("STAY", "roomy", 150_000, capacity=4, bedrooms=3),
            offer("STAY", "cramped", 80_000, capacity=3, bedrooms=3)]
    plain = compile_request(room, RoomRules(), pool, {})
    assert [c.id for c in plain.groups[0].options] == ["small", "roomy"], "4 people sharing two to a room need 2"
    own = compile_request(room, RoomRules(needs=(Need(owner="d", category="STAY", attribute="bedrooms", needs=1),)), pool, {})
    assert [c.id for c in own.groups[0].options] == ["roomy"], "d's own room + 3 sharing = 3 bedrooms"
    assert own.groups[0].excluded == {"capacity": 1, "bedrooms": 1}


def test_whole_trip_limits_sum_what_each_person_pays_into():
    room = Snapshot(room_id="r", members=("a", "b"), components=("FLIGHT", "STAY"), arrival_overlap_seconds=HOUR)
    rules = RoomRules(rules=(Rule(owner="a", aggregate="my_cost", operator="lte", value=60_000),))
    pool = [offer("FLIGHT", "a1", 30_000, "a", arrival_epoch=T0), offer("FLIGHT", "b1", 30_000, "b", arrival_epoch=T0),
            offer("STAY", "s1", 50_000, capacity=2, bedrooms=1)]
    request = compile_request(room, rules, pool, {"a": 0.4}).request
    cost = next(r for r in request["aggregate_rules"] if r["attribute"].startswith("my_cost"))
    assert cost["selection_group_ids"] == ["flight_0", "stay"], "a's own flight and the split stay, not b's flight"
    limit = next(c for c in request["constraints"] if c["attribute"] == cost["attribute"])
    assert (limit["owner_id"], limit["value"]) == ("a", 2 * 60_000), "party-size scaled"
    assert any(r["operation"] == "spread" for r in request["aggregate_rules"]), "the arrive-together group rule, from data"
    assert request["compromise_history"] == {"a": 0.4, "b": 0.0}


def _solve(room, rules, pool):
    from optimizer.multi_solver import optimize_multi
    compiled = compile_request(room, rules, pool, {})
    out = optimize_multi(compiled.request, cost_attribute="price_cents")
    return compiled, out


def test_the_same_wish_weighs_differently_by_each_persons_ranking():
    """Nonstop for $300 more: worth it to someone who puts comfort first, not to someone
    who puts affordability first. Same rule, same options; only their rankings differ."""
    room = Snapshot(room_id="r", members=("bella", "alex"), components=("FLIGHT",), arrival_overlap_seconds=10**9)
    pool = [offer("FLIGHT", f"{o}-{name}", price, owner=o, arrival_epoch=T0, stops=stops)
            for o in ("bella", "alex") for name, price, stops in (("nonstop", 70_000, 0), ("connect", 40_000, 1))]
    wish = lambda o: Rule(owner=o, category="FLIGHT", attribute="stops", operator="lte", value=0, kind="soft")
    rules = RoomRules(rules=(wish("bella"), wish("alex")),
                      ranking={"bella": ("comfort", "affordability"), "alex": ("affordability", "comfort")})
    _, out = _solve(room, rules, pool)
    assert sorted(out["proposals"][0]["candidate_ids"]) == ["alex-connect", "bella-nonstop"]


def test_an_option_missing_a_preferences_fact_stays_an_option():
    room = Snapshot(room_id="r", members=("a",), components=("FLIGHT",))
    pool = [offer("FLIGHT", "known", 50_000, owner="a", arrival_epoch=T0, stops=1),
            offer("FLIGHT", "unknown", 30_000, owner="a", arrival_epoch=T0)]
    rules = RoomRules(rules=(Rule(owner="a", category="FLIGHT", attribute="stops", operator="lte", value=0, kind="soft"),))
    compiled, out = _solve(room, rules, pool)
    assert out["status"] == "FEASIBLE" and out["proposals"][0]["candidate_ids"] == ["unknown"], "unknown counts as unmet, never as gone"
    assert {r["id"]: r["attributes"]["pref_0"] for r in compiled.request["candidates"]} == {"known": 0.0, "unknown": 0.0}


def test_as_short_as_possible_ranks_among_the_options_found():
    room = Snapshot(room_id="r", members=("a",), components=("FLIGHT",))
    pool = [offer("FLIGHT", f"f{m}", 40_000, owner="a", arrival_epoch=T0, duration_minutes=m) for m in (300, 400, 500)]
    rules = RoomRules(rules=(Rule(owner="a", category="FLIGHT", attribute="duration_minutes", operator="lowest", kind="soft"),))
    compiled, out = _solve(room, rules, pool)
    assert {r["id"]: r["attributes"]["pref_0"] for r in compiled.request["candidates"]} == {"f300": 1.0, "f400": 0.5, "f500": 0.0}
    assert out["proposals"][0]["candidate_ids"] == ["f300"]


def test_own_room_if_possible_is_weighed_never_required():
    from tandem_worker.rules import Need
    room = Snapshot(room_id="r", members=("a", "b", "c", "d"), components=("STAY",), people_per_bedroom=2)
    pool = [offer("STAY", "two", 100_000, capacity=4, bedrooms=2), offer("STAY", "three", 104_000, capacity=4, bedrooms=3)]
    wish = Need(owner="a", category="STAY", attribute="bedrooms", needs=1, kind="soft")
    compiled, out = _solve(room, RoomRules(needs=(wish,), ranking={"a": ("comfort",)}), pool)
    assert [c.id for c in compiled.groups[0].options] == ["two", "three"], "not required: the two-bedroom stays an option"
    assert out["proposals"][0]["candidate_ids"] == ["three"], "a small premium for what someone said they'd like"
