"""Owner questions, scoped grants and resume (G04–G06, R05, P07, P08)."""

import json

from conftest import Room, expect_error, key
from stdb_client import some


def ask(room: Room, worker, job_id: int, name: str, kind: str = "MISSING_FACT", action=None, thread_id=None) -> dict:
    request_key = key("q")
    room.client.call(worker, "ask_owner", job_id=job_id, attempt=room.leases[job_id], request_key=request_key,
                     member_id=room.member_ids[name], kind=kind,
                     action_json=json.dumps(action or {"action": "confirm_return_deadline"}),
                     prompt="Which Monday do you need to be home?",
                     answer_schema_json=json.dumps({"type": "object"}),
                     thread_id=some(thread_id) if thread_id else None)
    question = next(q for q in room.client.view(room.people[name], "my_questions") if q["request_key"] == request_key)
    return question


def park(room: Room, worker, job_id: int, question_ids: list[int]) -> None:
    room.client.call(worker, "park_job", job_id=job_id, attempt=room.leases[job_id], request_key=key("park"),
                     graph_thread_ref=f"graph-v1:room:{room.room_id}:generation:{job_id}:coordinator",
                     question_ids=question_ids)


def answer(room: Room, name: str, question: dict, payload: dict) -> None:
    room.client.call(room.people[name], "answer_question", question_id=question["id"], version=question["version"],
                     answer_json=json.dumps(payload))


def test_g04_question_parks_job_without_lease(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice")
    assert q["state"] == "OPEN" and q["kind"] == "MISSING_FACT"
    assert client.view(room.people["bob"], "my_questions") == []
    park(room, worker, job_id, [q["id"]])
    assert job_id not in {j["id"] for j in room.jobs(worker)}, "waiting jobs are not claimable"
    answer(room, "alice", q, {"date": "2026-11-02"})
    assert job_id in {j["id"] for j in room.jobs(worker)}, "answering makes the same generation claimable"
    resumed = room.claim(worker, job_id)
    assert resumed["graph_thread_ref"].endswith(f"generation:{job_id}:coordinator")


def test_r05_answer_before_parking(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice")
    answer(room, "alice", q, {"date": "2026-11-02"})
    park(room, worker, job_id, [q["id"]])
    assert job_id in {j["id"] for j in room.jobs(worker)}, "early answer must not strand the job"


def test_waits_for_every_question(ready_room, client, worker):
    room, job_id = ready_room
    q1, q2 = ask(room, worker, job_id, "alice"), ask(room, worker, job_id, "bob")
    park(room, worker, job_id, [q1["id"], q2["id"]])
    answer(room, "alice", q1, {"date": "2026-11-02"})
    assert job_id not in {j["id"] for j in room.jobs(worker)}
    answer(room, "bob", q2, {"date": "2026-11-03"})
    assert job_id in {j["id"] for j in room.jobs(worker)}


def test_wrong_owner_and_version(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice")
    expect_error("NOT_FOUND", client.call, room.people["bob"], "answer_question",
                 question_id=q["id"], version=q["version"], answer_json="{}")
    expect_error("QUESTION_SUPERSEDED", client.call, room.people["alice"], "answer_question",
                 question_id=q["id"], version=q["version"] + 1, answer_json="{}")
    answer(room, "alice", q, {"date": "x"})
    expect_error("QUESTION_SUPERSEDED", client.call, room.people["alice"], "answer_question",
                 question_id=q["id"], version=q["version"], answer_json="{}")


def test_p07_disclosure_answer_is_a_scoped_grant_not_consent(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice", kind="OBJECTION_DISCLOSURE", action={"action_ref": "draft-1"})
    park(room, worker, job_id, [q["id"]])
    answer(room, "alice", q, {"granted": True})
    # Granting changes what the agent may do, so planning restarts under a new
    # permission revision; the grant carries over for the same draft.
    assert job_id not in {j["id"] for j in room.jobs(worker)}
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    grants = room.context(worker, new_job)["grants"]
    assert [(g["question_id"], g["kind"], g["action_ref"], g["granted"]) for g in grants] == [
        (q["id"], "OBJECTION_DISCLOSURE", "draft-1", True)]
    assert client.view(room.people["alice"], "my_consents") == []


def test_declined_permission_resumes_the_same_generation(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice", kind="CONCESSION_CONSULTATION", action={"action_ref": "draft-1"})
    park(room, worker, job_id, [q["id"]])
    answer(room, "alice", q, {"granted": False})
    assert job_id in {j["id"] for j in room.jobs(worker)}
    room.claim(worker, job_id)
    assert room.context(worker, job_id)["grants"][0]["granted"] is False


def test_g06_answer_with_constraint_starts_new_generation(ready_room, client, worker):
    room, job_id = ready_room
    q = ask(room, worker, job_id, "alice")
    answer(room, "alice", q, {"constraint": {"key": "return_by", "value": "2026-11-02T18:00:00-10:00",
                                             "unit": "iso8601", "hardness": "HARD"}})
    expect_error("STALE_SNAPSHOT", room.context, worker, job_id)
    assert room.plan_job(worker) > job_id
    assert any(c["key"] == "return_by" for c in client.view(room.people["alice"], "my_constraints"))


def test_p08_preference_confirmation_bumps_permission_revision(ready_room, client, worker):
    room, job_id = ready_room
    before = room.context(worker, job_id)["job"]["permission_revision"]
    q = ask(room, worker, job_id, "bob", kind="PARTICIPATION_CONFIRMATION",
            action={"action": "confirm_participation"})
    answer(room, "bob", q, {"speak_up_for_me": False, "ask_me_first": True, "give_me_time": True})
    expect_error("STALE_SNAPSHOT", room.context, worker, job_id)
    new_job = room.plan_job(worker)
    room.claim(worker, new_job)
    ctx = room.context(worker, new_job)
    assert ctx["job"]["permission_revision"] == before + 1
    bob = next(m for m in ctx["members"] if m["member_id"] == room.member_ids["bob"])
    assert bob["tool_policy"]["may_object_in_room_without_grant"] is False


def test_asking_closes_the_thread(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    thread = next(t for t in client.view(room.people["alice"], "room_threads") if t["job_id"] == job_id)
    ask(room, worker, job_id, "alice", thread_id=thread["id"])
    thread = next(t for t in client.view(room.people["alice"], "room_threads") if t["id"] == thread["id"])
    assert (thread["state"], thread["closure_reason"]) == ("CLOSED", "WAITING_FOR_OWNER")
    expect_error("THREAD_CLOSED", room.send, worker, job_id, open_thread=None, thread_id=some(thread["id"]))
