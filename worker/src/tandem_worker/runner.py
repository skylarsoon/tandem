"""Single-worker runner; leases are independent of synchronous graph execution."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .contracts import ContractError, Limits
from .graphs.coordinator import build_coordinator
from .graphs.profile import run_profile
from .graphs.intake import run_intake
from .ports import SystemClock
from .progress import activity, report
from .solver import FlightSolver, FlightValidator

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class LeaseGuard:
    """Prevent new backend effects/calls after any heartbeat failure."""
    def __init__(self, backend, lost):
        self.backend, self.lost = backend, lost

    def __getattr__(self, name):
        attr = getattr(self.backend, name)
        if not callable(attr):
            return attr
        def guarded(*args, **kwargs):
            if self.lost.is_set():
                raise ContractError("LEASE_LOST")
            result = attr(*args, **kwargs)
            if self.lost.is_set():
                raise ContractError("LEASE_LOST")
            return result
        return guarded


class Worker:
    def __init__(self, backend, model, research, checkpoint_path, *, clock=None, limits=None, solver=None, validator=None, worker_id="local-worker", before_run=None):
        self.backend, self.model, self.research = backend, model, research
        self.checkpoint_path = str(checkpoint_path)
        self.clock, self.limits = clock or SystemClock(), limits or Limits()
        self.solver = solver or FlightSolver(self.limits.solver_seconds)
        self.validator = validator or FlightValidator()
        self.worker_id = worker_id
        self.before_run = before_run

    def run_one(self, job_id):
        lock_key = str(Path(self.checkpoint_path).resolve()) + ":" + job_id
        with _guard:
            lock = _locks.setdefault(lock_key, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ContractError("THREAD_ALREADY_RUNNING")
        try:
            return self._run(job_id)
        finally:
            lock.release()

    def _run(self, job_id):
        claim = self.backend.claim_job(job_id, self.worker_id, uuid.uuid4().hex)
        stopped = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not stopped.wait(self.limits.heartbeat_seconds):
                try:
                    self.backend.renew_lease(claim)
                except Exception:
                    # Every subsequent effect is independently fenced. Do not
                    # let a failed heartbeat generate more external calls.
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            if self.before_run is not None:
                self.before_run(claim)
            if claim.kind == "PROFILE":
                # The summary proposal itself completes the job.
                return {"outcome": run_profile(LeaseGuard(self.backend, lost), claim, self.model)}
            if claim.kind != "PLAN":
                # Onboarding is one short pass per message; no graph checkpoint needed.
                outcome = run_intake(LeaseGuard(self.backend, lost), claim, self.model)
                self.backend.finish_job(claim, outcome)
                return {"outcome": outcome}
            with SqliteSaver.from_conn_string(self.checkpoint_path) as saver:
                graph = build_coordinator(LeaseGuard(self.backend, lost), claim, self.model, self.research, self.solver,
                    self.validator, self.clock, self.limits, saver)
                config = {"configurable": {"thread_id": claim.graph_thread_ref}, "recursion_limit": 150}
                saved = graph.get_state(config)
                if saved.values:
                    report(self.backend, claim, "LOAD", "RECOVERING")
                interrupted = any(task.interrupts for task in saved.tasks)
                if interrupted:
                    refs = saved.values["questions"]
                    if any(self.backend.get_question(claim, q).answer is None for q in refs):
                        report(self.backend, claim, "AGENTS", "PAUSED", round=saved.values.get("round", 0), cycle=saved.values.get("cycle", 0))
                        activity(self.backend, claim, "mediator", "WAITING_FOR_OWNER")
                        self.backend.park_job(claim, refs)
                        return {"outcome": "WAITING_FOR_INPUT", "question_refs": refs}
                    graph_input = Command(resume={"answer_refs": refs})
                else:
                    graph_input = None if saved.values else {"job_id": job_id}
                result = graph.invoke(graph_input, config=config)
                if result.get("__interrupt__"):
                    refs = result["questions"]
                    self.backend.park_job(claim, refs)
                    return {"outcome": "WAITING_FOR_INPUT", "question_refs": refs}
                if result.get("proposal_ref"):
                    return {"outcome": "AWAITING_REVIEW", "proposal_ref": result["proposal_ref"]}
                outcome = result.get("outcome") or "UNRESOLVED"
                report(self.backend, claim, "STOPPED", "FINISHED")
                activity(self.backend, claim, "mediator", "IDLE")
                self.backend.finish_job(claim, outcome)
                return {"outcome": outcome, **({"validation_checks": result["validation_checks"]} if result.get("validation_checks") else {})}
        except ContractError as exc:
            if exc.code not in {"LEASE_LOST", "STALE_SNAPSHOT"}:
                try:
                    report(self.backend, claim, "STOPPED", "FAILED")
                    self.backend.finish_job(claim, exc.code)
                except ContractError:
                    pass
            return {"outcome": exc.code}
        except Exception:
            # Do not expose prompt/provider exception bodies to shared state.
            try:
                report(self.backend, claim, "STOPPED", "FAILED")
                self.backend.fail_job(claim, "EXECUTION_FAILED")
            except ContractError:
                pass
            raise
        finally:
            stopped.set()
            thread.join(timeout=self.limits.heartbeat_seconds + 1)

    def reconcile(self):
        results = []
        for job in self.backend.eligible_jobs():
            try:
                results.append(self.run_one(job))
            except ContractError as exc:
                if exc.code not in {"JOB_NOT_ELIGIBLE", "THREAD_ALREADY_RUNNING", "STALE_SNAPSHOT"}:
                    raise
        return results
