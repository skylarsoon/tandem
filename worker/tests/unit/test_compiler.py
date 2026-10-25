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
