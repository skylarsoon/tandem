import json
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from tandem_worker.backend import LocalBackend
from tandem_worker.contracts import ContractError, Limits, Preferences
from tandem_worker.fixtures import FixtureResearch, ScriptedModel, scenario_inputs
from tandem_worker.ports import FakeClock, SystemClock
from tandem_worker.runner import Worker


def test_duplicate_notifications_and_expired_attempt(setup_worker):
    backend, worker, job, _, _, clock = setup_worker()
    old = backend.claim_job(job, "worker-a", "one")
    with pytest.raises(ContractError, match="JOB_NOT_ELIGIBLE"):
        backend.claim_job(job, "worker-b", "two")
    clock.advance(61)
    replacement = backend.claim_job(job, "worker-b", "three")
    with pytest.raises(ContractError, match="LEASE_LOST"):
        backend.renew_lease(old)
    backend.check(replacement)


def test_atomic_claim_with_two_runners(setup_worker):
    backend, _, job, *_ = setup_worker()
    barrier = threading.Barrier(2)
    results = []
    def claim(worker):
        barrier.wait()
        try:
            results.append(backend.claim_job(job, worker, worker))
        except ContractError as exc:
            results.append(exc.code)
    threads = [threading.Thread(target=claim, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not isinstance(r, str) for r in results) == 1
    assert "JOB_NOT_ELIGIBLE" in results


def test_input_and_permission_edits_fence_old_attempts(setup_worker):
    backend, _, job, *_ = setup_worker()
    claim = backend.claim_job(job, "a", "a")
    new_job = backend.update_cap("p1", 46000)
    with pytest.raises(ContractError, match="LEASE_LOST"):
        backend.load_execution_context(claim)
    new_claim = backend.claim_job(new_job, "b", "b")
    backend.update_preferences("p1", Preferences(confirmed=True))
    with pytest.raises(ContractError, match="LEASE_LOST"):
        backend.load_execution_context(new_claim)


def test_cancel_during_call_suppresses_late_result(setup_worker):
    backend, worker, job, model, *_ = setup_worker()
    original = model.voice
    def cancel(context):
        backend.cancel(job)
        return original(context)
    model.voice = cancel
    assert worker.run_one(job)["outcome"] == "LEASE_LOST"
    assert backend.shared_report()["jobs"][0]["status"] == "CANCELLED"
    assert not backend.shared_report()["proposals"]


def test_lost_message_response_replays_receipt_after_restart(setup_worker):
    backend, worker, job, _, _, clock = setup_worker()
    original = backend.submit_agent_message
    committed = []
    def crash(*args):
        ref = original(*args)
        committed.append(ref)
        raise SystemExit("simulated process loss")
    backend.submit_agent_message = crash
    with pytest.raises(SystemExit):
        worker.run_one(job)
    clock.advance(61)
    reopened = LocalBackend(backend.path, clock)
    replacement = Worker(reopened, ScriptedModel(), FixtureResearch(clock, "happy"), worker.checkpoint_path, clock=clock)
    assert replacement.reconcile()[0]["outcome"] == "AWAITING_REVIEW"
    report = reopened.shared_report()
    assert len(report["messages"]) == 8
    assert sum(m["message_id"] == committed[0] for m in report["messages"]) == 1


def test_lost_publication_response_does_not_duplicate_proposal(setup_worker):
    backend, worker, job, *_ = setup_worker()
    original = backend.publish_proposal
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("simulated process loss")
    backend.publish_proposal = crash
    with pytest.raises(SystemExit):
        worker.run_one(job)
    assert worker.reconcile() == []
    assert len(backend.shared_report()["proposals"]) == 1


def test_ambiguous_external_operation_consumes_budget_on_reclaim(setup_worker):
    backend, _, job, _, _, clock = setup_worker(limits=Limits(model_calls=1))
    claim = backend.claim_job(job, "a", "a")
    def crash():
        raise SystemExit()
    with pytest.raises(SystemExit):
        backend.operation(claim, "external", {}, "model", crash)
    clock.advance(61)
    next_claim = backend.claim_job(job, "b", "b")
    with pytest.raises(ContractError, match="CALL_BUDGET_EXHAUSTED"):
        backend.operation(next_claim, "external", {}, "model", lambda: "repeated")


def test_early_answer_before_park_is_not_stranded(setup_worker):
    backend, worker, job, *_ = setup_worker("clarification-restart")
    original = backend.park_job
    def answer_then_park(claim, refs):
        for ref in refs:
            q = backend.get_question(claim, ref)
            backend.answer_question(q.owner, q.id, q.version, True, "early:" + q.id)
        original(claim, refs)
    backend.park_job = answer_then_park
    assert worker.run_one(job)["outcome"] == "WAITING_FOR_INPUT"
    backend.park_job = original
    assert worker.reconcile()[0]["outcome"] == "AWAITING_REVIEW"


def test_checkpoint_interrupt_before_park_recovers(setup_worker):
    backend, worker, job, _, _, clock = setup_worker("clarification-restart")
    def crash(*args):
        raise SystemExit()
    backend.park_job = crash
    with pytest.raises(SystemExit):
        worker.run_one(job)
    clock.advance(61)
    reopened = LocalBackend(backend.path, clock)
    replacement = Worker(reopened, ScriptedModel("clarification-restart"), FixtureResearch(clock, "clarification-restart"), worker.checkpoint_path, clock=clock)
    assert replacement.reconcile()[0]["outcome"] == "WAITING_FOR_INPUT"
    assert len(reopened.owner_questions("p1")) == 1


def test_clarification_across_two_python_processes(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(__import__('pathlib').Path(__file__).parents[2] / "src")}
    common = textwrap.dedent(f"""
        from pathlib import Path
        import pytest_socket
        pytest_socket.disable_socket()
        from tandem_worker.backend import LocalBackend
        from tandem_worker.fixtures import scenario_inputs, ScriptedModel, FixtureResearch
        from tandem_worker.ports import FakeClock
        from tandem_worker.runner import Worker
        path = Path({str(tmp_path)!r})
        clock = FakeClock()
        backend = LocalBackend(path / 'state.sqlite', clock)
        worker = Worker(backend, ScriptedModel('clarification-restart'), FixtureResearch(clock, 'clarification-restart'), path / 'checkpoints.sqlite', clock=clock)
    """)
    first = common + "\nsnapshot, briefs = scenario_inputs('clarification-restart')\njob = backend.seed(snapshot, briefs)\nprint(worker.run_one(job)['outcome'])\n"
    second = common + "\nq = backend.owner_questions('p1')[0]\nbackend.answer_question('p1', q.id, q.version, True, 'answer')\nprint(worker.reconcile()[0]['outcome'])\nprint(backend.shared_report()['consent_count'])\n"
    a = subprocess.run([sys.executable, "-c", first], env=environment, capture_output=True, text=True, check=True)
    b = subprocess.run([sys.executable, "-c", second], env=environment, capture_output=True, text=True, check=True)
    assert a.stdout.strip() == "WAITING_FOR_INPUT"
    assert b.stdout.strip().splitlines() == ["AWAITING_REVIEW", "0"]


def test_heartbeat_continues_during_blocking_model(tmp_path):
    limits = Limits(lease_seconds=0.3, heartbeat_seconds=0.04)
    clock = SystemClock()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock, limits)
    snapshot, briefs = scenario_inputs("happy")
    job = backend.seed(snapshot, briefs)
    count = []
    original = backend.renew_lease
    def heartbeat(claim):
        original(claim)
        count.append(True)
    backend.renew_lease = heartbeat
    model = ScriptedModel()
    voice = model.voice
    def slow(context):
        time.sleep(0.35)
        return voice(context)
    model.voice = slow
    worker = Worker(backend, model, FixtureResearch(clock, "happy"), tmp_path / "cp.sqlite", clock=clock, limits=limits)
    assert worker.run_one(job)["outcome"] == "AWAITING_REVIEW"
    assert len(count) >= 4


def test_failed_heartbeat_stops_work_even_before_lease_expiry(tmp_path):
    limits = Limits(lease_seconds=10, heartbeat_seconds=0.02)
    clock = SystemClock()
    backend = LocalBackend(tmp_path / "backend.sqlite", clock, limits)
    snapshot, briefs = scenario_inputs("happy")
    job = backend.seed(snapshot, briefs)
    def fail_heartbeat(claim):
        raise RuntimeError("synthetic transport failure")
    backend.renew_lease = fail_heartbeat
    model = ScriptedModel()
    original = model.voice
    def slow(context):
        time.sleep(0.1)
        return original(context)
    model.voice = slow
    worker = Worker(backend, model, FixtureResearch(clock, "happy"), tmp_path / "cp.sqlite", clock=clock, limits=limits)
    assert worker.run_one(job)["outcome"] == "LEASE_LOST"
    assert not backend.shared_report()["proposals"]
    assert not any(m["sender_agent_id"] != "mediator" for m in backend.shared_report()["messages"])
