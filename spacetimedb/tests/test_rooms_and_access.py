"""Rooms, membership, private inputs, and who may call what (C01, C02)."""

import json

from conftest import PEOPLE, Room, expect_error, key, make_room
from stdb_client import StdbError, some


def test_four_people_join_and_see_each_other(room: Room, client):
    for name in PEOPLE:
        members = [m for m in client.view(room.people[name], "room_members") if m["room_id"] == room.room_id]
        assert sorted(m["display_name"].lower() for m in members) == sorted(PEOPLE)
        assert [m["display_name"].lower() for m in members if m["is_me"]] == [name]


def test_join_rules(room: Room, client):
    expect_error("INVALID_STATE", client.call, room.people["bob"], "join_room",
                 invite_code=room.invite_code, display_name="Bob")
    outsider = client.mint_identity()
    expect_error("NOT_FOUND", client.call, outsider, "join_room", invite_code="NOPE1234", display_name="Eve")


def test_preferences_start_unconfirmed_with_safe_defaults(room: Room, client):
    prefs = client.view(room.people["bob"], "my_participation_preferences")[0]
    assert prefs["confirmed"] is False
    assert (prefs["speak_up_for_me"], prefs["ask_me_first"], prefs["give_me_time"]) == (False, True, True)


def test_c02_private_constraints_stay_private(room: Room, client):
    room.set_constraint("alice", "flight_budget_total", 50000, rationale="saving for rent")
    mine = client.view(room.people["alice"], "my_constraints")
    assert [(c["key"], c["private_rationale"]) for c in mine] == [("flight_budget_total", "saving for rent")]
    assert client.view(room.people["bob"], "my_constraints") == []
    # The underlying private table is not queryable at all.
    expect_error("SQL", client.sql, room.people["bob"], "SELECT * FROM participant_constraint")
    # Shared events say something changed without saying who or why.
    events = client.view(room.people["bob"], "room_events")
    shared = [json.loads(e["safe_payload_json"]) for e in events if e["type"] == "REQUIREMENTS_UPDATED"]
    assert shared and all("rent" not in json.dumps(p) for p in shared)


def test_c02_other_rooms_are_invisible(room: Room, client):
    other = make_room(client)
    rooms = {r["id"] for r in client.view(room.people["alice"], "my_rooms")}
    assert other.room_id not in rooms
    assert all(m["room_id"] != other.room_id for m in client.view(room.people["alice"], "room_members"))


def test_c01_worker_cannot_act_as_a_human(room: Room, client, worker):
    expect_error("NOT_AUTHORIZED", client.call, worker, "create_room", goal="x", display_name="Bot")
    expect_error("NOT_AUTHORIZED", client.call, worker, "set_constraint", room_id=room.room_id, key="k",
                 value_json="1", unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    expect_error("NOT_AUTHORIZED", client.call, worker, "respond_to_proposal", proposal_id=1, version=1,
                 terms_digest="x", response="ACCEPT", note=None)
    expect_error("NOT_AUTHORIZED", client.call, worker, "confirm_participation_preferences",
                 speak_up_for_me=True, ask_me_first=False, give_me_time=False, reminders_opted_in=False)


def test_humans_cannot_use_worker_operations(room: Room, client):
    alice = room.people["alice"]
    room.set_constraint("alice", "flight_budget_total", 50000)
    try:
        client.procedure(alice, "claim_job", job_id=None, lease_seconds=30, request_key=key())
        raise AssertionError("claim_job should fail for a human")
    except StdbError as e:
        assert e.code == "NOT_AUTHORIZED"
    assert client.view(alice, "worker_pending_jobs") == []
    expect_error("NOT_AUTHORIZED", client.call, alice, "submit_agent_message", **room.message_args(1, attempt=1))
    expect_error("NOT_AUTHORIZED", client.call, alice, "add_worker", identity=alice.arg, label="me")


def test_constraint_validation(room: Room, client):
    alice = room.people["alice"]
    expect_error("INVALID_ARGUMENT", client.call, alice, "set_constraint", room_id=room.room_id, key="k",
                 value_json="{not json", unit="", hardness="HARD", authorized_range_json=None, private_rationale=None)
    expect_error("INVALID_ARGUMENT", client.call, alice, "set_constraint", room_id=room.room_id, key="k",
                 value_json="1", unit="", hardness="MAYBE", authorized_range_json=None, private_rationale=None)
    outsider = client.mint_identity()
    expect_error("NOT_A_MEMBER", client.call, outsider, "set_constraint", room_id=room.room_id, key="k",
                 value_json="1", unit="", hardness="HARD", authorized_range_json=some("[1,2]"), private_rationale=None)
