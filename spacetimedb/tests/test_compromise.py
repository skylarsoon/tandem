"""Approved-compromise ledger: burdens count only after human agreement, once,
and a replaced decision supersedes its earlier contribution."""

from conftest import PEOPLE, Room, expect_error, key
from test_proposals import respond


def entries(room: Room, name: str) -> list[dict]:
    return sorted((e for e in room.client.view(room.people[name], "my_compromises") if e["room_id"] == room.room_id),
                  key=lambda e: e["id"])


def history(room: Room, worker) -> dict[str, float]:
    """H_i as the worker sees it, from a fresh PLAN job's context."""
    room.set_constraint("dave", "flight_budget_total", 65000 + len(room.leases))
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return room.context(worker, job_id)["compromise_history"]


def agree(room: Room, published: dict) -> None:
    for name in PEOPLE:
        respond(room, name, published)


def test_burden_counts_only_after_everyone_agrees(ready_room, worker):
    room, job_id = ready_room
    assert room.context(worker, job_id)["compromise_history"] == {str(room.member_ids[n]): 0 for n in PEOPLE}

    published = room.publish(worker, job_id, burdens={("bob", "STAY"): 0.6, ("carol", "STAY"): 0.25})
    for name in PEOPLE[:3]:
        respond(room, name, published)
    assert entries(room, "bob") == [], "a suggestion or a partial approval records nothing"

    respond(room, "dave", published)
    [bob] = entries(room, "bob")
    assert (bob["task_id"], bob["burden_micros"], bob["state"], bob["proposal_id"]) == \
        ("STAY", 600_000, "ACTIVE", published["proposal_id"])
    assert entries(room, "alice") == [], "people see only their own compromises"

    h = history(room, worker)
    assert h[str(room.member_ids["bob"])] == 0.6
    assert h[str(room.member_ids["carol"])] == 0.25
    assert h[str(room.member_ids["alice"])] == 0


def test_retries_never_double_count(ready_room, client, worker):
    room, job_id = ready_room
    request_key = key("publish")
    published = room.publish(worker, job_id, request_key=request_key, burdens={("bob", "STAY"): 0.5})
    again = room.publish(worker, job_id, request_key=request_key, burdens={("bob", "STAY"): 0.5})
    assert again == published
    agree(room, published)
    expect_error("INVALID_STATE", respond, room, "bob", published)  # already agreed: no second transition
    assert [e["burden_micros"] for e in entries(room, "bob")] == [500_000]


def test_withdrawn_approval_revokes_and_reapproval_restores(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id, burdens={("bob", "STAY"): 0.4})
    agree(room, published)
    client.call(room.people["carol"], "withdraw_consent", proposal_id=published["proposal_id"], note=None)
    [revoked] = entries(room, "bob")
    assert revoked["state"] == "REVOKED"

    respond(room, "carol", published)
    [restored] = entries(room, "bob")
    assert (restored["id"], restored["state"]) == (revoked["id"], "ACTIVE"), "same entry, not a second one"


def test_replacing_a_decision_supersedes_only_once_the_replacement_is_agreed(ready_room, worker):
    room, job_id = ready_room
    v1 = room.publish(worker, job_id, burdens={("bob", "STAY"): 0.6, ("bob", "DINNER"): 0.2})
    agree(room, v1)

    # Something changes; the planner publishes a new stay decision.
    room.set_constraint("alice", "flight_budget_total", 51000)
    job2 = room.plan_job(worker)
    room.claim(worker, job2)
    v2 = room.publish(worker, job2, burdens={("bob", "STAY"): 0.1, ("carol", "STAY"): 0.3})
    assert {e["task_id"]: e["state"] for e in entries(room, "bob")} == {"STAY": "ACTIVE", "DINNER": "ACTIVE"}, \
        "an unapproved replacement changes nothing"

    agree(room, v2)
    bob = {(e["task_id"], e["proposal_id"]): e["state"] for e in entries(room, "bob")}
    assert bob == {("STAY", v1["proposal_id"]): "SUPERSEDED", ("DINNER", v1["proposal_id"]): "ACTIVE",
                   ("STAY", v2["proposal_id"]): "ACTIVE"}
    h = history(room, worker)
    assert round(h[str(room.member_ids["bob"])], 6) == 0.3, "0.1 for the new stay + 0.2 for the untouched dinner"
    assert h[str(room.member_ids["carol"])] == 0.3


def test_publish_rejects_bad_burdens(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("INVALID_ARGUMENT", room.publish, worker, job_id, burdens={("bob", "STAY"): 1.5})
    expect_error("INVALID_ARGUMENT", room.publish, worker, job_id, burdens={("bob", ""): 0.5})
    args = dict(job_id=job_id, attempt=room.leases[job_id], request_key=key("publish"), stage="TRAVEL",
                terms_json="{}", shared_summary_json="{}", validation_json="{}", evidence_refs=[],
                affected_member_ids=[room.member_ids["alice"], room.member_ids["bob"]], impacts=[],
                selected_candidate_refs=[])
    twice = [{"member_id": room.member_ids["bob"], "task_id": "STAY", "burden_micros": 1}] * 2
    expect_error("INVALID_ARGUMENT", client.procedure, worker, "publish_proposal", **args, burdens=twice)
    not_reviewing = [{"member_id": room.member_ids["dave"], "task_id": "STAY", "burden_micros": 1}]
    expect_error("INVALID_ARGUMENT", client.procedure, worker, "publish_proposal", **args, burdens=not_reviewing)
