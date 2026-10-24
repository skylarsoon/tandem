"""Publication, consent and agreement (G01 backend side, C01, C03)."""

import json

from conftest import PEOPLE, Room, expect_error, key
from stdb_client import some


def respond(room: Room, name: str, published: dict, response: str = "ACCEPT", **overrides) -> None:
    args = dict(proposal_id=published["proposal_id"], version=published["version"],
                terms_digest=published["terms_digest"], response=response, note=None)
    args.update(overrides)
    room.client.call(room.people[name], "respond_to_proposal", **args)


def counts(room: Room, name: str, proposal_id: int) -> dict:
    return next(c for c in room.client.view(room.people[name], "room_consent_counts")
                if c["proposal_id"] == proposal_id)


def milestone(room: Room, stage: str = "TRAVEL") -> dict:
    return next(m for m in room.client.view(room.people["alice"], "room_milestones")
                if m["room_id"] == room.room_id and m["stage"] == stage)


def test_full_agreement_cycle(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    published = room.publish(worker, job_id)
    assert published["version"] == 1

    proposals = [p for p in client.view(room.people["bob"], "room_proposals") if p["room_id"] == room.room_id]
    assert [(p["state"], p["reviewer_count"], p["is_reviewer"]) for p in proposals] == [("AWAITING_REVIEW", 4, True)]
    assert "validation_json" not in proposals[0]
    assert milestone(room)["state"] == "IN_REVIEW"
    assert job_id not in {j["id"] for j in room.jobs(worker)}
    # Publishing ends the job: the worker can't keep writing under it.
    expect_error("LEASE_LOST", room.send, worker, job_id)

    for name in PEOPLE[:3]:
        respond(room, name, published)
    c = counts(room, "dave", published["proposal_id"])
    assert (c["required"], c["accepted"], c["pending"]) == (4, 3, 1)
    assert milestone(room)["state"] == "IN_REVIEW", "three of four is not agreement"

    respond(room, "dave", published)
    agreed = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert agreed["state"] == "AGREED"
    assert milestone(room)["state"] == "AGREED"

    client.call(room.people["carol"], "withdraw_consent", proposal_id=published["proposal_id"], note=None)
    reopened = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert reopened["state"] == "AWAITING_REVIEW"
    assert milestone(room)["state"] == "IN_REVIEW"
    assert room.jobs(worker, kind="INTERPRET"), "withdrawal asks carol's agent to follow up"


def test_publish_is_idempotent(ready_room, client, worker):
    room, job_id = ready_room
    request_key = key("publish")
    first = room.publish(worker, job_id, request_key=request_key)
    again = room.publish(worker, job_id, request_key=request_key)
    assert first == again
    assert len([p for p in client.view(room.people["alice"], "room_proposals") if p["room_id"] == room.room_id]) == 1


def test_impacts_are_private(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    for name in PEOPLE:
        impacts = [i for i in client.view(room.people[name], "my_proposal_impacts")
                   if i["proposal_id"] == published["proposal_id"]]
        assert len(impacts) == 1


def test_c03_stale_or_wrong_terms_never_count(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    expect_error("STALE_SNAPSHOT", respond, room, "alice", published, terms_digest="0" * 64)
    expect_error("STALE_SNAPSHOT", respond, room, "alice", published, version=published["version"] + 1)
    respond(room, "alice", published)

    room.set_constraint("bob", "flight_budget_total", 60000)
    expect_error("STALE_SNAPSHOT", respond, room, "carol", published)
    consents = {c["response"] for c in client.view(room.people["alice"], "my_consents")
                if c["proposal_id"] == published["proposal_id"]}
    assert consents == {"STALE"}
    superseded = next(p for p in client.view(room.people["alice"], "room_proposals") if p["id"] == published["proposal_id"])
    assert superseded["state"] == "SUPERSEDED"
    assert milestone(room)["state"] == "REOPENED"


def test_declines_and_decide_later(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    respond(room, "alice", published, "DECIDE_LATER")
    respond(room, "bob", published, "NOT_COMFORTABLE", note=some("arrival is too late for me"))
    respond(room, "carol", published)
    respond(room, "dave", published)
    c = counts(room, "carol", published["proposal_id"])
    assert (c["accepted"], c["declined"], c["pending"]) == (2, 1, 1)
    mine = next(c for c in client.view(room.people["alice"], "my_consents") if c["proposal_id"] == published["proposal_id"])
    assert mine["response"] == "PENDING" and mine["decide_later_available"] is False
    bob_private = [m["body"] for m in client.view(room.people["bob"], "my_private_messages")]
    assert any("NOT_COMFORTABLE" in body and "too late" in body for body in bob_private)
    assert all("too late" not in json.dumps(e) for e in client.view(room.people["carol"], "room_events"))
    expect_error("INVALID_STATE", client.call, room.people["alice"], "withdraw_consent",
                 proposal_id=published["proposal_id"], note=None)


def test_newer_version_supersedes_older(ready_room, client, worker):
    room, job_id = ready_room
    first = room.publish(worker, job_id)
    room.confirm_all()  # new permission revision → new generation
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    second = room.publish(worker, new_job, terms={"flights": {"alice": "UA9"}})
    assert second["version"] == first["version"] + 1
    states = {p["id"]: p["state"] for p in client.view(room.people["alice"], "room_proposals")}
    assert states[first["proposal_id"]] == "SUPERSEDED" and states[second["proposal_id"]] == "AWAITING_REVIEW"


def test_outsider_and_worker_cannot_respond(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id)
    outsider = client.mint_identity()
    expect_error("NOT_A_MEMBER", client.call, outsider, "respond_to_proposal", proposal_id=published["proposal_id"],
                 version=published["version"], terms_digest=published["terms_digest"], response="ACCEPT", note=None)
    expect_error("NOT_AUTHORIZED", client.call, worker, "respond_to_proposal", proposal_id=published["proposal_id"],
                 version=published["version"], terms_digest=published["terms_digest"], response="ACCEPT", note=None)
