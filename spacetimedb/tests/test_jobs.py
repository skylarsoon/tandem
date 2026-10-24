"""Job claims, leases, snapshots and receipts (W01–W03, R01, U02)."""

import json
import time

from conftest import PEOPLE, Room, expect_error, key
from stdb_client import some


def test_constraint_change_creates_a_new_generation(room: Room, client, worker):
    room.set_constraint("alice", "flight_budget_total", 50000)
    first = room.plan_job(worker)
    room.set_constraint("bob", "flight_budget_total", 65000)
    second = room.plan_job(worker)
    assert second > first
    pending_ids = {j["id"] for j in room.jobs(worker)}
    assert first not in pending_ids, "the older generation must be superseded"


def test_w01_competing_claims(ready_room, client, workers):
    room, job_id = ready_room
    _, other = workers
    expect_error("JOB_NOT_ELIGIBLE", client.procedure, other, "claim_job",
                 job_id=some(job_id), lease_seconds=30, request_key=key())


def test_claim_replay_returns_the_same_attempt(room: Room, client, worker):
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    request_key = key("claim")
    first = client.procedure(worker, "claim_job", job_id=some(job_id), lease_seconds=30, request_key=request_key)
    again = client.procedure(worker, "claim_job", job_id=some(job_id), lease_seconds=30, request_key=request_key)
    assert first == again
    expect_error("KEY_BODY_MISMATCH", client.procedure, worker, "claim_job",
                 job_id=some(job_id), lease_seconds=45, request_key=request_key)


def test_w02_expired_lease_fences_the_old_attempt(room: Room, client, workers):
    first, second = workers
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(first)
    old = room.claim(first, job_id, lease=2)
    time.sleep(3)
    new = client.procedure(second, "claim_job", job_id=some(job_id), lease_seconds=30, request_key=key())
    assert new["attempt"] == old["attempt"] + 1
    expect_error("LEASE_LOST", client.call, first, "renew_lease", job_id=job_id, attempt=old["attempt"], lease_seconds=30)
    expect_error("LEASE_LOST", client.call, first, "submit_agent_message",
                 **room.message_args(job_id, attempt=old["attempt"]))
    room.leases[job_id] = new["attempt"]
    room.send(second, job_id)


def test_expired_lease_returns_job_to_pending(room: Room, client, worker):
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    room.claim(worker, job_id, lease=2)
    assert job_id not in {j["id"] for j in room.jobs(worker)}
    time.sleep(3)
    assert job_id in {j["id"] for j in room.jobs(worker)}, "the scheduled expiry should requeue the job"


def test_w03_input_change_mid_run_rejects_worker_writes(ready_room, client, worker):
    room, job_id = ready_room
    room.set_constraint("alice", "flight_budget_total", 46000)
    expect_error("STALE_SNAPSHOT", room.context, worker, job_id)
    expect_error("STALE_SNAPSHOT", room.send, worker, job_id)
    assert room.plan_job(worker) > job_id


def test_execution_context_snapshot(ready_room, client, worker):
    room, job_id = ready_room
    ctx = room.context(worker, job_id)
    assert ctx["job"]["job_id"] == job_id and ctx["job"]["generation"] == job_id
    assert ctx["mediator_agent_id"] == room.mediator_id
    members = {m["display_name"].lower(): m for m in ctx["members"]}
    assert set(members) == set(PEOPLE)
    alice = members["alice"]
    assert alice["agent_id"] == room.agent_ids["alice"]
    assert alice["participation_preferences"]["confirmed"] is True
    assert alice["tool_policy"] == {
        "may_speak_in_room": True,
        "may_object_in_room_without_grant": True,
        "consult_before_visible_concession": False,
        "reminders_allowed": False,  # reminders are opt-in
    }
    assert ctx["room"]["settings"] == {"arrival_overlap_seconds": 7200}
    caps = {c["member_id"]: c["value"] for c in ctx["constraints"]}
    assert caps[room.member_ids["alice"]] == 50000


def test_finish_and_fail(room: Room, client, worker):
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    attempt = room.claim(worker, job_id)["attempt"]
    client.call(worker, "fail_job", job_id=job_id, attempt=attempt, request_key=key(), retryable=True,
                code="PROVIDER_TIMEOUT", detail="retry me")
    assert job_id in {j["id"] for j in room.jobs(worker)}
    attempt = room.claim(worker, job_id)["attempt"]
    finish_key = key("finish")
    finish = dict(job_id=job_id, attempt=attempt, request_key=finish_key, outcome="NO_FEASIBLE_CANDIDATE",
                  outcome_json=json.dumps({"checked": 12}))
    client.call(worker, "finish_job", **finish)
    client.call(worker, "finish_job", **finish)  # replay is a no-op
    expect_error("KEY_BODY_MISMATCH", client.call, worker, "finish_job", **{**finish, "outcome": "OTHER"})
    expect_error("LEASE_LOST", client.call, worker, "fail_job", job_id=job_id, attempt=attempt, request_key=key(),
                 retryable=False, code="X", detail="too late")


def test_lease_bounds(room: Room, client, worker):
    room.confirm_all()
    room.set_constraint("alice", "flight_budget_total", 50000)
    job_id = room.plan_job(worker)
    expect_error("INVALID_ARGUMENT", client.procedure, worker, "claim_job",
                 job_id=some(job_id), lease_seconds=1, request_key=key())


def begin(room: Room, worker, job_id: int, op_key: str, digest: str = "d1", kind: str = "model", limit: int = 2) -> dict:
    return room.client.procedure(worker, "begin_operation", job_id=job_id, attempt=room.leases[job_id], op_key=op_key,
                                 request_digest=digest, kind=kind, limit=limit)


def test_operation_ledger_reuses_results_and_enforces_budgets(ready_room, client, workers):
    room, job_id = ready_room
    first, second = workers
    assert begin(room, first, job_id, "decide:1")["status"] == "RESERVED"
    client.call(first, "complete_operation", job_id=job_id, attempt=room.leases[job_id], op_key="decide:1",
                result_json=json.dumps({"act": "EVALUATE"}))
    replay = begin(room, first, job_id, "decide:1")
    assert replay["status"] == "DONE" and json.loads(replay["result_json"]) == {"act": "EVALUATE"}
    expect_error("KEY_BODY_MISMATCH", begin, room, first, job_id, "decide:1", digest="other")

    # An unfinished reservation is ambiguous: retrying reserves (and spends) again.
    assert begin(room, first, job_id, "decide:2")["status"] == "RESERVED"
    expect_error("CALL_BUDGET_EXHAUSTED", begin, room, first, job_id, "decide:2")
    # Local work (e.g. the solver) never counts against model/research budgets.
    assert begin(room, first, job_id, "solve:0", kind="local")["status"] == "RESERVED"

    # Budgets survive a crash: a re-claimed attempt keeps the same counters.
    client.call(first, "renew_lease", job_id=job_id, attempt=room.leases[job_id], lease_seconds=2)
    time.sleep(3)
    room.claim(second, job_id)
    assert begin(room, second, job_id, "decide:1")["status"] == "DONE"
    expect_error("CALL_BUDGET_EXHAUSTED", begin, room, second, job_id, "decide:3")


def test_check_lease_and_close_threads(ready_room, client, worker):
    room, job_id = ready_room
    assert client.procedure(worker, "check_lease", job_id=job_id, attempt=room.leases[job_id])["job_id"] == job_id
    room.send(worker, job_id)
    client.call(worker, "close_threads", job_id=job_id, attempt=room.leases[job_id], reason="NO_PROGRESS")
    thread = room.context(worker, job_id)["threads"][0]
    assert (thread["state"], thread["closure_reason"]) == ("CLOSED", "NO_PROGRESS")
    expect_error("THREAD_CLOSED", room.send, worker, job_id, open_thread=None, thread_id=some(thread["thread_id"]))
    expect_error("LEASE_LOST", client.procedure, worker, "check_lease", job_id=job_id, attempt=99)


def test_publish_and_park_close_open_threads(ready_room, client, worker):
    room, job_id = ready_room
    room.send(worker, job_id)
    room.publish(worker, job_id)
    threads = [t for t in client.view(room.people["alice"], "room_threads") if t["job_id"] == job_id]
    assert [(t["state"], t["closure_reason"]) for t in threads] == [("CLOSED", "RESOLVED")]
