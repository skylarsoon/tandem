"""Persistent profile onboarding: answers checked against the question catalog,
private to their owner, skipped kept distinct from "none", and the agent's
"Does this sound like you?" summary the owner confirms or corrects."""
import json

from conftest import expect_error, key
from stdb_client import some
from test_intake import described_room, intake_job, propose


def answer(client, who, qid, value):
    client.call(who, "save_profile_answer", question_id=qid, value_json=json.dumps(value))


def answers(client, who) -> dict:
    return {a["question_id"]: (a["status"], json.loads(a["value_json"])) for a in client.view(who, "my_profile_answers")}


def calibration(client, who) -> dict | None:
    rows = client.view(who, "my_profile_calibration")
    return rows[0] if rows else None


def test_answers_are_checked_against_the_catalog(client):
    sam = client.mint_identity()
    answer(client, sam, "C01", "Sam")
    answer(client, sam, "C02", {"selected": ["food", "beaches", "other"], "other": "tide pools"})
    expect_error("INVALID_ANSWER", answer, client, sam, "C02", {"selected": ["food", "dragons"]})
    expect_error("INVALID_ANSWER", answer, client, sam, "C02", {"selected": ["food", "still_exploring"]})
    expect_error("INVALID_ANSWER", answer, client, sam, "C02", {"selected": ["food", "nature", "hiking", "beaches", "wellness", "sports"]})
    expect_error("INVALID_ANSWER", answer, client, sam, "C02", {"selected": ["food"], "other": "needs the Other choice"})
    # A ranking is only a preference once it's explicitly confirmed.
    expect_error("INVALID_ANSWER", answer, client, sam, "C03", {"ranked": ["affordability", "comfort"]})
    answer(client, sam, "C03", {"ranked": ["affordability", "comfort"], "confirmed": True})
    # "Own room" must say whether it's a must or a preference.
    expect_error("INVALID_ANSWER", answer, client, sam, "T06", {"selected": "own_room"})
    answer(client, sam, "T06", {"selected": "own_room", "strength": "REQUIRED"})
    answer(client, sam, "N01", {"items": [{"food": "peanuts", "checks": ["avoid_ingredient", "cross_contact"]}]})
    expect_error("INVALID_ANSWER", answer, client, sam, "N02", {"items": [{"id": "vegan"}]})
    answer(client, sam, "N02", {"items": [{"id": "vegan", "strength": "PREFERRED"}]})
    answer(client, sam, "A06", {"city": "San Francisco", "country": "USA", "airports": ["SFO", "OAK"]})
    expect_error("INVALID_ANSWER", answer, client, sam, "A06", {"city": "SF", "country": "USA", "airports": ["sfo"]})
    answer(client, sam, "E05", {"start": "early", "evenings": "flexible"})
    expect_error("INVALID_ANSWER", answer, client, sam, "T01", {"selected": ["walking"], "avoid": ["walking"]})
    expect_error("INVALID_ARGUMENT", answer, client, sam, "Z99", "?")

    saved = answers(client, sam)
    assert saved["T06"] == ("ANSWERED", {"selected": "own_room", "strength": "REQUIRED"})
    profile = client.view(sam, "my_profile")[0]
    assert (profile["display_name"], profile["priority_ranking"]) == ("Sam", ["affordability", "comfort"])


def test_skipped_none_and_unanswered_stay_distinct_and_private(client):
    sam, other = client.mint_identity(), client.mint_identity()
    answer(client, sam, "C01", "Sam")
    expect_error("INVALID_STATE", client.call, sam, "skip_profile_question", question_id="C01")
    client.call(sam, "skip_profile_question", question_id="C04")
    answer(client, sam, "C08", {"selected": ["none"]})
    saved = answers(client, sam)
    assert saved["C04"] == ("SKIPPED", None) and saved["C08"] == ("ANSWERED", {"selected": ["none"]})
    assert "C05" not in saved, "unanswered is simply absent"
    client.call(sam, "clear_profile_answer", question_id="C04")
    assert "C04" not in answers(client, sam)
    assert answers(client, other) == {}, "nobody sees anyone else's answers"


def test_the_agent_summary_is_confirmed_or_corrected_by_its_owner(client, worker):
    sam = client.mint_identity()
    expect_error("INVALID_STATE", client.call, sam, "request_profile_summary")
    answer(client, sam, "C01", "Sam")
    answer(client, sam, "C02", {"selected": ["food", "beaches"]})
    answer(client, sam, "N01", {"items": [{"food": "peanuts", "checks": ["avoid_ingredient"]}]})
    client.call(sam, "request_profile_summary")
    assert calibration(client, sam)["state"] == "PENDING"

    [job] = [j for j in client.view(worker, "worker_pending_jobs") if j["kind"] == "PROFILE"]
    claimed = client.procedure(worker, "claim_job", job_id=some(job["id"]), lease_seconds=60, request_key=key())
    ctx = client.procedure(worker, "load_profile_context", job_id=job["id"], attempt=claimed["attempt"])
    assert {a["question_id"] for a in ctx["answers"]} == {"C01", "C02", "N01"}
    client.call(worker, "propose_profile_summary", job_id=job["id"], attempt=claimed["attempt"], request_key=key(),
                summary="You're Sam: food and beaches first, and peanuts are always off the table.")
    shown = calibration(client, sam)
    assert shown["state"] == "PROPOSED" and "peanuts" in shown["summary"]

    # Answers changed after the summary was written: confirming it is refused.
    answer(client, sam, "C02", {"selected": ["food"]})
    expect_error("STALE_SNAPSHOT", client.call, sam, "confirm_profile_summary", profile_revision=shown["profile_revision"])

    # A correction asks for a fresh summary; the old pending job is cancelled.
    client.call(sam, "correct_profile_summary", correction="I'm more of a mountains person, actually.")
    again = calibration(client, sam)
    assert again["state"] == "PENDING"
    [job2] = [j for j in client.view(worker, "worker_pending_jobs") if j["kind"] == "PROFILE"]
    claimed2 = client.procedure(worker, "claim_job", job_id=some(job2["id"]), lease_seconds=60, request_key=key())
    ctx2 = client.procedure(worker, "load_profile_context", job_id=job2["id"], attempt=claimed2["attempt"])
    assert ctx2["calibration"]["correction"] == "I'm more of a mountains person, actually."
    client.call(worker, "propose_profile_summary", job_id=job2["id"], attempt=claimed2["attempt"], request_key=key(),
                summary="You're Sam: food first, mountains over beaches, and no peanuts.")
    fresh = calibration(client, sam)
    client.call(sam, "confirm_profile_summary", profile_revision=fresh["profile_revision"])
    assert calibration(client, sam)["state"] == "CONFIRMED"


def test_rooms_see_profile_answers_only_as_suggestions(room, client, worker):
    answer(client, room.people["dave"], "C01", "Dave")
    answer(client, room.people["dave"], "A06", {"city": "New York", "country": "USA", "airports": ["JFK"]})
    described_room(room, worker)
    job_id = intake_job(room, worker, "dave")
    ctx = room.context(worker, job_id)
    assert ctx["subject"]["profile"]["A06"]["airports"] == ["JFK"]
    patch = {"key": "origin_airport", "value": "JFK", "unit": "IATA", "hardness": "HARD", "source_profile": "A06"}
    pending = propose(room, worker, job_id, "dave", patch)
    assert any(p["id"] == pending and p["state"] == "PENDING" for p in client.view(room.people["dave"], "my_constraint_patches")), \
        "a suggestion waits for Dave to confirm it for this trip"
    expect_error("INVALID_EVIDENCE_REF", propose, room, worker, job_id, "dave", {**patch, "source_profile": "T06"})


def test_a_planning_agent_knows_its_person_only_by_what_they_confirmed(ready_room, client, worker):
    room, job_id = ready_room
    dave = room.people["dave"]
    answer(client, dave, "C01", "Dave")
    answer(client, dave, "C03", {"ranked": ["comfort", "affordability"], "confirmed": True})
    client.call(dave, "request_profile_summary")
    [job] = [j for j in client.view(worker, "worker_pending_jobs") if j["kind"] == "PROFILE"]
    claimed = client.procedure(worker, "claim_job", job_id=some(job["id"]), lease_seconds=60, request_key=key())
    client.call(worker, "propose_profile_summary", job_id=job["id"], attempt=claimed["attempt"], request_key=key(),
                summary="You're Dave: comfort first, then cost.")

    def his(ctx):
        return next(m for m in ctx["members"] if m["member_id"] == room.member_ids["dave"])["profile"]
    assert his(room.context(worker, job_id))["summary"] is None, "proposed, not yet confirmed"
    client.call(dave, "confirm_profile_summary", profile_revision=calibration(client, dave)["profile_revision"])
    profile = his(room.context(worker, job_id))
    assert profile["summary"] == "You're Dave: comfort first, then cost."
    assert {a["question_id"] for a in profile["answers"]} == {"C01", "C03"}
    answer(client, dave, "C03", {"ranked": ["affordability"], "confirmed": True})
    assert his(room.context(worker, job_id))["summary"] is None, "his answers moved on: the old summary isn't him any more"
