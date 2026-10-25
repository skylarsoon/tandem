"""Rules in the generic language: old named limits mirror into rules, rules can
be added directly, and publication checks picks against them (and against what
shared picks must hold) with the same evaluator the worker uses."""
import json

from conftest import PEOPLE, Room, expect_error
from stdb_client import some
from test_proposals import publish_selected, record_shared, trip_room

RATING = {"category": "STAY", "attribute": "rating", "operator": "gte", "value": 4.5}


def my_rules(room: Room, name: str) -> list[dict]:
    return [r for r in room.client.view(room.people[name], "my_rules") if r["room_id"] == room.room_id]


def add_rule(room: Room, name: str, rule: dict, quote: str | None = None):
    room.client.call(room.people[name], "add_rule", room_id=room.room_id, rule_json=json.dumps(rule),
                     source_quote=some(quote) if quote else None)


def stay_job(room: Room, client, worker) -> int:
    client.call(room.people["alice"], "set_room_settings", room_id=room.room_id,
                settings_json=json.dumps({"components": ["FLIGHT", "STAY"]}))
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return job_id


def test_named_limits_mirror_into_rules_and_setting_one_aside_removes_it(ready_room, worker):
    room, _ = ready_room
    [cap] = [r for r in my_rules(room, "alice") if r["legacy_key"] == "flight_budget_total"]
    assert json.loads(cap["rule_json"]) == {"category": "FLIGHT", "attribute": "total_cents", "operator": "lte",
                                            "value": 50000, "kind": "hard"}
    room.client.call(room.people["bob"], "set_constraint", room_id=room.room_id, key="stay_min_rating", value_json="4.5",
                     unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    assert any(r["legacy_key"] == "stay_min_rating" for r in my_rules(room, "bob"))
    room.client.call(room.people["bob"], "set_constraint", room_id=room.room_id, key="stay_min_rating", value_json="null",
                     unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    assert not any(r["legacy_key"] == "stay_min_rating" for r in my_rules(room, "bob")), "set aside: no rule"
    assert my_rules(room, "carol") and all(r["room_id"] == room.room_id for r in my_rules(room, "carol"))


def test_a_rule_added_directly_is_enforced_at_publication(ready_room, client, worker):
    room, _ = ready_room
    expect_error("INVALID_RULE", add_rule, room, "bob", {"category": "STAY", "attribute": "pool", "operator": "eq", "value": True})
    expect_error("INVALID_RULE", add_rule, room, "bob", {"category": "STAY", "attribute": "rating", "operator": "lt", "value": "great"})
    add_rule(room, "bob", RATING, "somewhere nice, 4.5 stars at least")
    [rule] = [r for r in my_rules(room, "bob") if r["legacy_key"] is None]
    assert rule["source_quote"] == "somewhere nice, 4.5 stars at least"
    job_id = stay_job(room, client, worker)
    shared = dict(scope="SINGLE_SHARED_BOOKING", decision_id="stay", stage="STAY_EXPERIENCES")
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000, rating=4.2)], **shared)
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000)], **shared)
    ok = publish_selected(room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000, rating=4.7)], **shared)
    assert ok["version"] == 1
    client.call(room.people["bob"], "remove_rule", rule_id=rule["id"])
    assert not any(r["legacy_key"] is None for r in my_rules(room, "bob"))


def test_shared_picks_hold_what_everyone_needs(ready_room, client, worker):
    room, _ = ready_room
    room.client.call(room.people["dave"], "set_constraint", room_id=room.room_id, key="own_bedroom", value_json="true",
                     unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    job_id = stay_job(room, client, worker)
    ctx = room.context(worker, job_id)
    assert ctx["needs"] == [{"member_id": room.member_ids["dave"], "category": "STAY", "attribute": "bedrooms", "needs": 1.0}]
    assert ctx["rules"] and not any(r["legacy_key"] == "own_bedroom" for r in ctx["rules"]), "a need, not a rule"
    shared = dict(scope="SINGLE_SHARED_BOOKING", decision_id="stay", stage="STAY_EXPERIENCES")
    # Dave's own room + the other three sharing two to a room = 1 + 2 = 3 bedrooms.
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000, bedrooms=2)], **shared)
    assert publish_selected(room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000, bedrooms=3)], **shared)["version"] == 1


def test_onboarding_can_capture_any_checkable_limit_as_a_rule(room, client, worker):
    from test_intake import described_room, intake_job, propose, say, summarize
    described_room(room, worker)
    job_id = intake_job(room, worker, "dave")
    said = say(room, "dave", "I fly from JFK. Whole trip under $900 please, and somewhere rated 4.5 or better.")
    propose(room, worker, job_id, "dave", {"key": "origin_airport", "value": "JFK", "unit": "", "hardness": "HARD",
                                           "source_quote": "JFK"}, said)
    rating = propose(room, worker, job_id, "dave", {"rule": RATING, "source_quote": "rated 4.5 or better"}, said)
    budget = propose(room, worker, job_id, "dave", {"rule": {"aggregate": "my_cost", "operator": "lte", "value": 90000},
                                                    "source_quote": "under $900"}, said)
    expect_error("INVALID_RULE", propose, room, worker, job_id, "dave",
                 {"rule": {"category": "STAY", "attribute": "pool", "operator": "eq", "value": True}, "source_quote": "JFK"}, said)
    summarize(room, worker, job_id, "dave")
    patches = [p["id"] for p in client.view(room.people["dave"], "my_constraint_patches") if p["state"] == "PENDING"]
    client.call(room.people["dave"], "confirm_intake", room_id=room.room_id, accepted_patch_ids=patches,
                speak_up_for_me=some(False), ask_me_first=some(True), give_me_time=some(True), reminders_opted_in=None)
    mine = {r["source_quote"]: json.loads(r["rule_json"]) for r in my_rules(room, "dave") if r["legacy_key"] is None}
    assert mine["rated 4.5 or better"] == {**RATING, "kind": "hard"}
    assert mine["under $900"]["aggregate"] == "my_cost", "a money rule covers money: no named budget needed"
    assert {rating, budget} <= set(patches)


def test_a_rule_set_aside_stops_binding(ready_room, client, worker):
    room, _ = ready_room
    add_rule(room, "bob", RATING)
    [rule] = [r for r in my_rules(room, "bob") if r["legacy_key"] is None]
    job_id = stay_job(room, client, worker)
    shared = dict(scope="SINGLE_SHARED_BOOKING", decision_id="stay", stage="STAY_EXPERIENCES")
    low = record_shared(room, worker, job_id, "STAY", 90000, rating=4.0)
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id, [low], **shared)
    client.call(room.people["bob"], "set_rule_state", rule_id=rule["id"], state="set_aside")
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    assert publish_selected(room, worker, job_id, [record_shared(room, worker, job_id, "STAY", 90000, rating=4.0)], **shared)
    [cap] = [r for r in my_rules(room, "bob") if r["legacy_key"] == "flight_budget_total"]
    expect_error("INVALID_STATE", client.call, room.people["bob"], "set_rule_state", rule_id=cap["id"], state="set_aside")
    expect_error("INVALID_ARGUMENT", client.call, room.people["bob"], "set_rule_state", rule_id=rule["id"], state="forgotten")


def test_a_kept_rule_still_binds_and_a_preference_keeps_its_weight(ready_room, client, worker):
    room, _ = ready_room
    add_rule(room, "bob", RATING)
    prefer = {"category": "STAY", "attribute": "rating", "operator": "gte", "value": 4.8, "kind": "soft",
              "importance": 4, "tolerance": {"limit": 4.0}}
    add_rule(room, "bob", prefer)
    expect_error("INVALID_RULE", add_rule, room, "bob", {**RATING, "importance": 4})  # a must has no weight
    mine = {json.loads(r["rule_json"])["kind"]: r for r in my_rules(room, "bob") if r["legacy_key"] is None}
    assert json.loads(mine["soft"]["rule_json"]) == prefer
    # Asked to relax it, Bob keeps it: "kept" is still in force.
    client.call(room.people["bob"], "set_rule_state", rule_id=mine["hard"]["id"], state="kept")
    job_id = stay_job(room, client, worker)
    shared = dict(scope="SINGLE_SHARED_BOOKING", decision_id="stay", stage="STAY_EXPERIENCES")
    expect_error("INVALID_PROPOSAL", publish_selected, room, worker, job_id,
                 [record_shared(room, worker, job_id, "STAY", 90000, rating=4.0)], **shared)


def test_a_later_statement_about_the_same_thing_replaces_the_earlier_one(ready_room, client, worker):
    room, _ = ready_room
    leave = {"category": "FLIGHT", "attribute": "departure_date", "operator": "gte"}
    add_rule(room, "carol", {**leave, "value": "2026-11-21"}, "I can't leave before November 21")
    add_rule(room, "carol", {**leave, "value": "2026-11-25"}, "I can't depart until November 25 now")
    add_rule(room, "carol", {**leave, "value": "2026-11-22", "kind": "soft"})  # a wish sits beside the must
    mine = [json.loads(r["rule_json"]) for r in my_rules(room, "carol") if r["legacy_key"] is None]
    assert sorted((r["value"], r["kind"]) for r in mine) == [("2026-11-22", "soft"), ("2026-11-25", "hard")]
