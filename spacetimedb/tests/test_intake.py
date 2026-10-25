"""Conversational onboarding: trip brief, per-traveller intake, origin quick-confirm,
batched summary confirmation, quoted evidence, and the publish gate."""
import json

from conftest import PEOPLE, Room, expect_error, key
from stdb_client import some

BRIEF = {"destination": "Honolulu", "destination_airport": "HNL", "start_date": "2027-02-12",
         "end_date": "2027-02-16", "components": ["FLIGHT", "STAY", "TRANSFER"], "vibe": "chill beach trip"}


def intake_job(room: Room, worker, name: str) -> int:
    jobs = [j for j in room.jobs(worker, "INTAKE")]
    for j in sorted(jobs, key=lambda j: -j["id"]):
        room.claim(worker, j["id"])
        ctx = room.context(worker, j["id"])
        if ctx["subject"] and ctx["subject"]["member_id"] == room.member_ids[name]:
            return j["id"]
    raise AssertionError(f"no intake job for {name}")


def brief_job(room: Room, worker) -> dict:
    """The organizer's open trip-description job (newest first; others are member onboarding)."""
    for j in sorted(room.jobs(worker, "INTAKE"), key=lambda j: -j["id"]):
        room.claim(worker, j["id"])
        if room.context(worker, j["id"])["subject"]["purpose"] == "BRIEF":
            return j
    raise AssertionError("no trip-description job")


def described_room(room: Room, worker) -> None:
    """Organizer describes the trip, the agent proposes the brief, the organizer confirms."""
    room.client.call(room.people["alice"], "set_trip_description", room_id=room.room_id,
                     description="Four of us, Honolulu Feb 12-16, chill beach trip, a place together.")
    [job] = room.jobs(worker, "INTAKE")
    room.claim(worker, job["id"])
    ctx = room.context(worker, job["id"])
    assert ctx["subject"]["purpose"] == "BRIEF" and ctx["trip_brief"]["state"] == "DRAFT"
    room.client.call(worker, "propose_trip_brief", job_id=job["id"], attempt=room.leases[job["id"]],
                     request_key=key(), brief_json=json.dumps(BRIEF), unclear_json="[]")
    [brief] = [b for b in room.client.view(room.people["bob"], "room_trip_brief") if b["room_id"] == room.room_id]
    assert brief["state"] == "PROPOSED"
    room.client.call(room.people["alice"], "confirm_trip_brief", room_id=room.room_id, revision=brief["revision"])


def say(room: Room, name: str, body: str) -> int:
    room.client.call(room.people[name], "submit_private_message", room_id=room.room_id, body=body)
    return max(m["id"] for m in room.client.view(room.people[name], "my_private_messages") if m["room_id"] == room.room_id)


def propose(room: Room, worker, job_id: int, name: str, patch: dict, source: int | None = None):
    room.client.call(worker, "propose_constraint_patch", job_id=job_id, attempt=room.leases[job_id], request_key=key(),
                     member_id=room.member_ids[name], patch_json=json.dumps(patch),
                     source_message_id=some(source) if source is not None else None)
    return max(p["id"] for p in room.client.view(room.people[name], "my_constraint_patches"))


def summarize(room: Room, worker, job_id: int, name: str, coverage=None, asked=False):
    coverage = coverage or {"origin": "ANSWERED", "dates": "ANSWERED", "money": "ANSWERED", "settings": "ANSWERED"}
    room.client.call(worker, "update_intake", job_id=job_id, attempt=room.leases[job_id], request_key=key(),
                     member_id=room.member_ids[name], coverage_json=json.dumps(coverage), state="SUMMARY", asked_question=asked)


def test_confirmed_brief_sets_room_rules_and_starts_everyones_onboarding(room, client, worker):
    expect_error("NOT_AUTHORIZED", client.call, room.people["bob"], "set_trip_description",
                 room_id=room.room_id, description="mine now")
    described_room(room, worker)
    ctx_job = intake_job(room, worker, "dave")
    ctx = room.context(worker, ctx_job)
    settings = ctx["room"]["settings"]
    assert (settings["destination_airport"], settings["start_date"], settings["components"]) == (
        "HNL", "2027-02-12", ["FLIGHT", "STAY", "TRANSFER"])
    assert ctx["subject"]["purpose"] == "INTAKE" and ctx["subject"]["intake"]["state"] == "ASKING"
    assert ctx["subject"]["intake"]["coverage"] == {"origin": "OPEN", "dates": "OPEN", "money": "OPEN", "settings": "OPEN"}
    for name in PEOPLE:
        [mine] = [i for i in client.view(room.people[name], "my_intake") if i["room_id"] == room.room_id]
        assert mine["state"] == "ASKING"
    [progress] = [p for p in client.view(room.people["bob"], "room_intake_progress") if p["room_id"] == room.room_id]
    assert (progress["ready"], progress["total"]) == (0, 4)


def test_onboarding_context_holds_only_the_subjects_conversation(room, client, worker):
    described_room(room, worker)
    say(room, "bob", "I'm saving for rent, so nothing crazy")
    say(room, "dave", "Flying from JFK, keep it under $700 all in")
    job = intake_job(room, worker, "dave")
    subject = room.context(worker, job)["subject"]
    assert [m["body"] for m in subject["messages"] if m["sender"] == "HUMAN"] == ["Flying from JFK, keep it under $700 all in"]
    assert "rent" not in json.dumps(subject)


def test_patches_must_quote_the_owner_exactly(room, client, worker):
    described_room(room, worker)
    said = say(room, "dave", "Flying from JFK, keep it under $700 all in")
    job = intake_job(room, worker, "dave")
    budget = {"key": "trip_budget_total", "value": 70000, "unit": "USD_cents", "hardness": "HARD"}
    expect_error("INVALID_EVIDENCE_REF", propose, room, worker, job, "dave", {**budget, "source_quote": "under $800 all in"}, said)
    expect_error("INVALID_EVIDENCE_REF", propose, room, worker, job, "dave", {**budget, "source_quote": "under $700 all in"})
    bob_said = say(room, "bob", "under $700 all in")
    expect_error("INVALID_EVIDENCE_REF", propose, room, worker, job, "dave", {**budget, "source_quote": "under $700 all in"}, bob_said)
    propose(room, worker, job, "dave", {**budget, "source_quote": "under $700 all in"}, said)


def test_origin_quick_confirm_then_one_batched_summary(room, client, worker):
    described_room(room, worker)
    said = say(room, "dave", "Flying from JFK, keep it under $700 all in")
    job = intake_job(room, worker, "dave")
    origin = propose(room, worker, job, "dave", {"key": "origin_airport", "value": "JFK", "hardness": "HARD",
                                                 "source_quote": "Flying from JFK"}, said)
    budget = propose(room, worker, job, "dave", {"key": "trip_budget_total", "value": 70000, "unit": "USD_cents",
                                                 "hardness": "HARD", "source_quote": "under $700 all in"}, said)
    expect_error("INVALID_ARGUMENT", client.call, room.people["dave"], "confirm_origin", room_id=room.room_id, patch_id=budget)
    client.call(room.people["dave"], "confirm_origin", room_id=room.room_id, patch_id=origin)
    assert [c["key"] for c in client.view(room.people["dave"], "my_constraints") if c["room_id"] == room.room_id] == ["origin_airport"]

    # The summary is confirmed in one go; it needs agent settings and money covered.
    expect_error("INVALID_STATE", client.call, room.people["dave"], "confirm_intake", room_id=room.room_id,
                 accepted_patch_ids=[budget], speak_up_for_me=None, ask_me_first=None, give_me_time=None, reminders_opted_in=None)
    summarize(room, worker, job, "dave")
    expect_error("PREFERENCES_UNCONFIRMED", client.call, room.people["dave"], "confirm_intake", room_id=room.room_id,
                 accepted_patch_ids=[budget], speak_up_for_me=None, ask_me_first=None, give_me_time=None, reminders_opted_in=None)
    expect_error("INTAKE_INCOMPLETE", client.call, room.people["dave"], "confirm_intake", room_id=room.room_id,
                 accepted_patch_ids=[], speak_up_for_me=some(True), ask_me_first=some(True), give_me_time=some(False),
                 reminders_opted_in=None)
    plans_before = len(room.jobs(worker))
    client.call(room.people["dave"], "confirm_intake", room_id=room.room_id, accepted_patch_ids=[budget],
                speak_up_for_me=some(True), ask_me_first=some(True), give_me_time=some(False), reminders_opted_in=None)
    keys = sorted(c["key"] for c in client.view(room.people["dave"], "my_constraints") if c["room_id"] == room.room_id)
    assert keys == ["origin_airport", "trip_budget_total"]
    assert len(room.jobs(worker)) == 1 and plans_before <= 1, "one live PLAN job, not one per confirmed item"
    [progress] = [p for p in client.view(room.people["alice"], "room_intake_progress") if p["room_id"] == room.room_id]
    assert progress["ready"] == 1


def test_withdraw_clears_stale_patches_and_question_cap_holds(room, client, worker):
    described_room(room, worker)
    said = say(room, "carol", "Maybe $900?")
    job = intake_job(room, worker, "carol")
    stale = propose(room, worker, job, "carol", {"key": "trip_budget_total", "value": 90000, "hardness": "SOFT",
                                                 "source_quote": "$900"}, said)
    client.call(worker, "withdraw_constraint_patches", job_id=job, attempt=room.leases[job], request_key=key(),
                member_id=room.member_ids["carol"])
    [patch] = [p for p in client.view(room.people["carol"], "my_constraint_patches") if p["id"] == stale]
    assert patch["state"] == "REJECTED"
    open_ = {"origin": "OPEN", "dates": "OPEN", "money": "UNCLEAR", "settings": "OPEN"}
    for _ in range(5):
        client.call(worker, "update_intake", job_id=job, attempt=room.leases[job], request_key=key(),
                    member_id=room.member_ids["carol"], coverage_json=json.dumps(open_), state="ASKING", asked_question=True)
    expect_error("TURN_BUDGET_EXHAUSTED", client.call, worker, "update_intake", job_id=job, attempt=room.leases[job],
                 request_key=key(), member_id=room.member_ids["carol"], coverage_json=json.dumps(open_), state="ASKING",
                 asked_question=True)


def test_late_joiner_gets_onboarding_and_publish_waits_for_everyone(room, client, worker):
    described_room(room, worker)
    late = client.mint_identity()
    client.call(late, "join_room", invite_code=room.invite_code, display_name="Erin")
    assert [i["state"] for i in client.view(late, "my_intake") if i["room_id"] == room.room_id] == ["ASKING"]
    room.set_constraint("alice", "flight_budget_total", 50000)
    job = room.plan_job(worker)
    room.claim(worker, job)
    expect_error("INTAKE_INCOMPLETE", room.publish, worker, job)


def test_rooms_without_a_brief_keep_todays_behaviour(ready_room, worker):
    room, job_id = ready_room
    assert room.publish(worker, job_id)["version"] == 1


def test_a_brief_that_leaves_dates_out_keeps_the_rooms_dates(room, client, worker):
    described_room(room, worker)
    room.client.call(room.people["alice"], "set_trip_description", room_id=room.room_id,
                     description="Same trip, but let's also get a ride from the airport.")
    job = brief_job(room, worker)
    without_dates = {k: v for k, v in BRIEF.items() if k not in ("start_date", "end_date")}
    room.client.call(worker, "propose_trip_brief", job_id=job["id"], attempt=room.leases[job["id"]],
                     request_key=key(), brief_json=json.dumps(without_dates), unclear_json="[]")
    [brief] = [b for b in client.view(room.people["alice"], "room_trip_brief") if b["room_id"] == room.room_id]
    room.client.call(room.people["alice"], "confirm_trip_brief", room_id=room.room_id, revision=brief["revision"])
    [mine] = [r for r in client.view(room.people["alice"], "my_rooms") if r["id"] == room.room_id]
    settings = json.loads(mine["settings_json"])
    assert (settings["start_date"], settings["end_date"]) == ("2027-02-12", "2027-02-16")


def test_dates_out_of_order_are_refused(room, client, worker):
    room.client.call(room.people["alice"], "set_trip_description", room_id=room.room_id, description="Honolulu trip")
    [job] = room.jobs(worker, "INTAKE")
    room.claim(worker, job["id"])
    backwards = dict(BRIEF, start_date="2027-02-16", end_date="2027-02-12")
    expect_error("INVALID_ARGUMENT", room.client.call, worker, "propose_trip_brief", job_id=job["id"],
                 attempt=room.leases[job["id"]], request_key=key(), brief_json=json.dumps(backwards), unclear_json="[]")


def test_the_organizers_order_and_arrival_choice_become_room_settings(room, client, worker):
    room.client.call(room.people["alice"], "set_trip_description", room_id=room.room_id,
                     description="Flights first, then a place, then activities. Separate arrivals are fine.")
    job = brief_job(room, worker)
    bad = dict(BRIEF, stages=["FLIGHT", "YACHT"])
    expect_error("INVALID_ARGUMENT", room.client.call, worker, "propose_trip_brief", job_id=job["id"],
                 attempt=room.leases[job["id"]], request_key=key(), brief_json=json.dumps(bad), unclear_json="[]")
    staged = dict(BRIEF, components=["FLIGHT", "STAY"], stages=["FLIGHT", "STAY", "ACTIVITY"], arrive_together="off", activities=3)
    room.client.call(worker, "propose_trip_brief", job_id=job["id"], attempt=room.leases[job["id"]],
                     request_key=key(), brief_json=json.dumps(staged), unclear_json="[]")
    [brief] = [b for b in client.view(room.people["alice"], "room_trip_brief") if b["room_id"] == room.room_id]
    room.client.call(room.people["alice"], "confirm_trip_brief", room_id=room.room_id, revision=brief["revision"])
    [mine] = [r for r in client.view(room.people["alice"], "my_rooms") if r["id"] == room.room_id]
    settings = json.loads(mine["settings_json"])
    assert (settings["stages"], settings["arrive_together"], settings["activities"]) == (["FLIGHT", "STAY", "ACTIVITY"], "off", 3)
