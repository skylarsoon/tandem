"""Approved-compromise ledger: burdens count only after human agreement, once,
and a new version of a decision replaces its earlier contribution."""

from conftest import PEOPLE, Room, expect_error, key
from test_proposals import respond


def entries(room: Room, name: str) -> list[dict]:
    return sorted((e for e in room.client.view(room.people[name], "my_compromises") if e["room_id"] == room.room_id),
                  key=lambda e: e["id"])


def new_job(room: Room, worker) -> int:
    """A fresh PLAN job; agreed decisions are untouched by the input change."""
    room.set_constraint("dave", "flight_budget_total", 65000 + len(room.leases))
    job_id = room.plan_job(worker)
    room.claim(worker, job_id)
    return job_id


def history(room: Room, worker) -> dict[str, float]:
    """H_i as the worker sees it."""
    return room.context(worker, new_job(room, worker))["compromise_history"]


def agree(room: Room, published: dict) -> None:
    for name in PEOPLE:
        respond(room, name, published)


def test_burden_counts_only_after_everyone_agrees(ready_room, worker):
    room, job_id = ready_room
    assert room.context(worker, job_id)["compromise_history"] == {str(room.member_ids[n]): 0 for n in PEOPLE}

    published = room.publish(worker, job_id, decision_id="stay", burdens={"bob": 0.6, "carol": 0.25})
    for name in PEOPLE[:3]:
        respond(room, name, published)
    assert entries(room, "bob") == [], "a suggestion or a partial approval records nothing"

    respond(room, "dave", published)
    [bob] = entries(room, "bob")
    assert (bob["decision_id"], bob["burden_micros"], bob["state"], bob["proposal_id"]) == \
        ("stay", 600_000, "ACTIVE", published["proposal_id"])
    assert entries(room, "alice") == [], "people see only their own compromises"

    h = history(room, worker)
    assert h[str(room.member_ids["bob"])] == 0.6
    assert h[str(room.member_ids["carol"])] == 0.25
    assert h[str(room.member_ids["alice"])] == 0


def test_retries_never_double_count(ready_room, worker):
    room, job_id = ready_room
    request_key = key("publish")
    published = room.publish(worker, job_id, request_key=request_key, burdens={"bob": 0.5})
    assert room.publish(worker, job_id, request_key=request_key, burdens={"bob": 0.5}) == published
    agree(room, published)
    expect_error("INVALID_STATE", respond, room, "bob", published)  # already agreed: no second transition
    assert [e["burden_micros"] for e in entries(room, "bob")] == [500_000]


def test_withdrawn_approval_revokes_and_reapproval_restores(ready_room, client, worker):
    room, job_id = ready_room
    published = room.publish(worker, job_id, burdens={"bob": 0.4})
    agree(room, published)
    client.call(room.people["carol"], "withdraw_consent", proposal_id=published["proposal_id"], note=None)
    [revoked] = entries(room, "bob")
    assert revoked["state"] == "REVOKED"

    respond(room, "carol", published)
    [restored] = entries(room, "bob")
    assert (restored["id"], restored["state"]) == (revoked["id"], "ACTIVE"), "same entry, not a second one"


def test_a_new_version_of_a_decision_replaces_its_compromise(ready_room, worker):
    room, job_id = ready_room
    stay = room.publish(worker, job_id, decision_id="stay", burdens={"bob": 0.6})
    agree(room, stay)
    dinner = room.publish(worker, new_job(room, worker), decision_id="dinner", burdens={"bob": 0.2})
    agree(room, dinner)

    revised = room.publish(worker, new_job(room, worker), decision_id="stay", burdens={"bob": 0.1, "carol": 0.3})
    bob = {e["proposal_id"]: e["state"] for e in entries(room, "bob")}
    assert bob == {stay["proposal_id"]: "SUPERSEDED", dinner["proposal_id"]: "ACTIVE"}, \
        "replacing the stay stops its old compromise counting; the dinner is untouched"

    agree(room, revised)
    h = history(room, worker)
    assert round(h[str(room.member_ids["bob"])], 6) == 0.3, "0.1 for the new stay + 0.2 for the dinner"
    assert h[str(room.member_ids["carol"])] == 0.3


def test_publish_rejects_bad_burdens(ready_room, client, worker):
    room, job_id = ready_room
    expect_error("INVALID_ARGUMENT", room.publish, worker, job_id, burdens={"bob": 1.5})
    args = dict(job_id=job_id, attempt=room.leases[job_id], request_key=key("publish"), stage="TRAVEL",
                decision_id="travel", scope="GENERAL_AGREEMENT", terms_json="{}", shared_summary_json="{}",
                validation_json="{}", evidence_refs=[], impacts=[], selected_candidate_refs=[],
                affected_member_ids=[room.member_ids["alice"], room.member_ids["bob"]])
    twice = [{"member_id": room.member_ids["bob"], "burden_micros": 1}] * 2
    expect_error("INVALID_ARGUMENT", client.procedure, worker, "publish_proposal", **args, burdens=twice)
    not_reviewing = [{"member_id": room.member_ids["dave"], "burden_micros": 1}]
    expect_error("INVALID_ARGUMENT", client.procedure, worker, "publish_proposal", **args, burdens=not_reviewing)
