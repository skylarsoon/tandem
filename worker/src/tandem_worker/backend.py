"""Persistent LOCAL backend for fixtures and contract tests, not SpacetimeDB.

One synthetic room per file. Transactions enforce application invariants and
receipts. No model import or natural-language judgment belongs in this module.
Human identities are supplied by the test harness; this is not production auth.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .contracts import Brief, Candidate, Claim, ContractError, Limits, Message, Preferences, Question, Snapshot, ValidationReport, digest
from .ports import Clock, SystemClock


def primitive(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


class LocalBackend:
    def __init__(self, path: str | Path, clock: Clock | None = None, limits: Limits | None = None):
        self.path = str(path)
        self.clock = clock or SystemClock()
        self.limits = limits or Limits()
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")

    @contextmanager
    def _tx(self):
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body FROM state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {}
            yield state
            conn.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (json.dumps(state, sort_keys=True),))

    def seed(self, snapshot: Snapshot, briefs: list[Brief]) -> str:
        if {b.owner for b in briefs} != set(snapshot.members):
            raise ContractError("INVALID_MEMBERSHIP")
        with self._tx() as state:
            if state:
                raise ContractError("ALREADY_SEEDED")
            state.update(snapshot=primitive(snapshot), briefs={b.owner: primitive(b) for b in briefs},
                         jobs={}, receipts={}, messages={}, threads={}, questions={}, operations={},
                         candidates={}, candidate_context={}, proposals={}, consents=[], grants={}, events=[], sequence=0)
            return self._new_job(state)

    def _new_job(self, state):
        generation = digest(state["snapshot"])[:16]
        job_id = f"job-{generation}"
        state["jobs"].setdefault(job_id, dict(id=job_id, generation=generation, snapshot=state["snapshot"].copy(),
            snapshot_ref=digest(state["snapshot"]), status="QUEUED", token=None, worker=None, expiry=0,
            questions=[], calls={"model": 0, "research": 0}))
        return job_id

    def _checked(self, state, claim):
        job = state["jobs"].get(claim.job_id)
        if not job or job["status"] != "RUNNING" or job["token"] != claim.attempt_token or job["worker"] != claim.worker_id or job["expiry"] <= self.clock.now():
            raise ContractError("LEASE_LOST")
        if digest(state["snapshot"]) != claim.snapshot_ref:
            raise ContractError("STALE_SNAPSHOT")
        return job

    def _receipt(self, state, key, body):
        found = state["receipts"].get(key)
        if found and found["digest"] != digest(body):
            raise ContractError("KEY_BODY_MISMATCH")
        return found

    def _record(self, state, key, body, result):
        state["receipts"][key] = {"digest": digest(body), "result": result}
        return result

    def claim_job(self, job_id, worker_id, request_key):
        with self._tx() as state:
            key = f"claim:{worker_id}:{request_key}"
            body = {"job": job_id, "worker": worker_id}
            old = self._receipt(state, key, body)
            if old:
                claim = Claim.model_validate(old["result"])
                self._checked(state, claim)
                return claim
            job = state["jobs"][job_id]
            if job["snapshot_ref"] != digest(state["snapshot"]):
                raise ContractError("STALE_SNAPSHOT")
            eligible = job["status"] == "QUEUED" or (job["status"] == "RUNNING" and job["expiry"] <= self.clock.now())
            if not eligible:
                raise ContractError("JOB_NOT_ELIGIBLE")
            job.update(status="RUNNING", worker=worker_id, token=uuid.uuid4().hex, expiry=self.clock.now() + self.limits.lease_seconds)
            claim = Claim(job_id=job_id, worker_id=worker_id, attempt_token=job["token"], generation=job["generation"],
                snapshot_ref=job["snapshot_ref"], lease_expiry=job["expiry"],
                graph_thread_ref=f"graph-v1:{state['snapshot']['room_id']}:{job['generation']}:coordinator")
            self._record(state, key, body, primitive(claim))
            return claim

    def check(self, claim):
        with self._tx() as state:
            self._checked(state, claim)

    def load_execution_context(self, claim):
        with self._tx() as state:
            return Snapshot.model_validate(self._checked(state, claim)["snapshot"])

    def read_brief(self, claim, owner):
        with self._tx() as state:
            self._checked(state, claim)
            if owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            return Brief.model_validate(state["briefs"][owner])

    def renew_lease(self, claim):
        with self._tx() as state:
            self._checked(state, claim)["expiry"] = self.clock.now() + self.limits.lease_seconds

    def operation(self, claim, key, request, kind, function):
        """Receipt-backed external operation. Ambiguous retries reserve again.

        Store request hashes, never private prompts. A RUNNING record with no
        result denotes possible external cost, not proof the call never ran.
        """
        op_key = f"{claim.generation}:{key}"
        with self._tx() as state:
            job = self._checked(state, claim)
            op = state["operations"].get(op_key)
            if op and op["request_digest"] != digest(request):
                raise ContractError("KEY_BODY_MISMATCH")
            if op and op["status"] == "DONE":
                return op["result"]
            if kind in ("model", "research"):
                limit = self.limits.model_calls if kind == "model" else self.limits.research_calls
                if job["calls"][kind] >= limit:
                    raise ContractError("CALL_BUDGET_EXHAUSTED")
                job["calls"][kind] += 1
            state["operations"][op_key] = dict(status="RUNNING", request_digest=digest(request), kind=kind)
        result = primitive(function())
        with self._tx() as state:
            self._checked(state, claim)
            state["operations"][op_key].update(status="DONE", result=result)
        return result

    def submit_agent_message(self, claim, message, participants):
        # Validate even if a caller bypassed Pydantic's constructor.
        message = Message.model_validate(primitive(message))
        body = primitive(message)
        key = f"message:{message.idempotency_key}"
        with self._tx() as state:
            self._checked(state, claim)
            old = self._receipt(state, key, {"message": body, "participants": participants})
            if old:
                return old["result"]
            snapshot = Snapshot.model_validate(state["snapshot"])
            if message.room_id != snapshot.room_id or (message.input_revision, message.membership_revision, message.permission_revision) != (snapshot.input_revision, snapshot.membership_revision, snapshot.permission_revision):
                raise ContractError("STALE_SNAPSHOT")
            permitted_agents = {*snapshot.members, "mediator"}
            if not participants or len(set(participants)) != len(participants) or not set(participants) <= permitted_agents or message.sender_agent_id not in participants or message.recipient_agent_id not in participants or message.sender_agent_id == message.recipient_agent_id:
                raise ContractError("NOT_AUTHORIZED")
            if message.message_id in state["messages"]:
                raise ContractError("KEY_BODY_MISMATCH")
            if len(message.utterance) > 4096:
                raise ContractError("PAYLOAD_TOO_LARGE")
            if message.worker_validation.snapshot_ref != claim.snapshot_ref or message.worker_validation.permission_revision != snapshot.permission_revision or message.worker_validation.content_digest != digest(message.checked_content()):
                raise ContractError("INVALID_VALIDATION_METADATA")
            if any(ref not in state["candidates"] for ref in (*message.evidence_refs, *message.payload.candidate_refs)):
                raise ContractError("INVALID_EVIDENCE_REF")
            if message.audience == "PUBLIC_SUMMARY":
                for owner in participants:
                    if owner == "mediator":
                        continue
                    prefs = Preferences.model_validate(state["briefs"][owner]["preferences"])
                    if not prefs.confirmed:
                        raise ContractError("PREFERENCES_UNCONFIRMED")
                if message.sender_agent_id != "mediator":
                    prefs = Preferences.model_validate(state["briefs"][message.sender_agent_id]["preferences"])
                    for kind in self._required_permissions(message, prefs):
                        if not self._has_grant(state, message.sender_agent_id, kind, message.payload.draft_ref):
                            raise ContractError("CONSULTATION_REQUIRED")
            thread = state["threads"].get(message.thread_id)
            if thread:
                if thread["state"] != "OPEN":
                    raise ContractError("THREAD_CLOSED")
                if thread["participants"] != list(participants) or thread["round"] != message.round_id:
                    raise ContractError("NOT_AUTHORIZED")
            else:
                thread = dict(participants=list(participants), state="OPEN", turns=0, round=message.round_id, generation=claim.generation)
            if message.reply_to:
                parent = state["messages"].get(message.reply_to)
                if not parent or parent["message"]["thread_id"] != message.thread_id or parent["message"]["recipient_agent_id"] != message.sender_agent_id:
                    raise ContractError("INVALID_REPLY")
            round_turns = sum(t["turns"] for t in state["threads"].values() if t["generation"] == claim.generation and t["round"] == message.round_id)
            if thread["turns"] >= self.limits.thread_turns or round_turns >= self.limits.round_turns:
                raise ContractError("TURN_BUDGET_EXHAUSTED")
            thread["turns"] += 1
            state["threads"][message.thread_id] = thread
            state["sequence"] += 1
            state["messages"][message.message_id] = {"message": body, "sequence": state["sequence"], "state": "RECEIVED", "reply_refs": []}
            return self._record(state, key, {"message": body, "participants": participants}, message.message_id)

    @staticmethod
    def _required_permissions(message, prefs):
        result = []
        if message.type == "OBJECT" and not prefs.speak_up_for_me:
            result.append("DISCLOSURE")
        if message.payload.concession and prefs.ask_me_first:
            result.append("CONSULTATION")
        if message.payload.exceeds_exploration:
            result.append("EXPLORE")
        return result

    def record_message_processed(self, claim, message_id, recipient, replies):
        with self._tx() as state:
            self._checked(state, claim)
            row = state["messages"][message_id]
            if row["message"]["recipient_agent_id"] != recipient or any(ref not in state["messages"] for ref in replies):
                raise ContractError("NOT_AUTHORIZED")
            if row["state"] == "PROCESSED" and row["reply_refs"] != list(replies):
                raise ContractError("KEY_BODY_MISMATCH")
            row.update(state="PROCESSED", reply_refs=list(replies))

    def store_candidates(self, claim, candidates):
        with self._tx() as state:
            self._checked(state, claim)
            for candidate in candidates:
                candidate = Candidate.model_validate(primitive(candidate))
                if candidate.owner not in state["snapshot"]["members"]:
                    raise ContractError("NOT_AUTHORIZED")
                old = state["candidates"].get(candidate.id)
                if old and old != primitive(candidate):
                    raise ContractError("CANDIDATE_ID_CONFLICT")
                state["candidates"][candidate.id] = primitive(candidate)
                state["candidate_context"][candidate.id] = [state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]

    def reusable_candidates(self, claim):
        with self._tx() as state:
            self._checked(state, claim)
            revisions = [state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]
            # This local backend only supports preference permission edits;
            # all fixture evidence is already public. A real projection must
            # additionally check current source/sharing/search authorization.
            return [Candidate.model_validate(c) for ref, c in state["candidates"].items()
                    if state["candidate_context"].get(ref) == revisions and c["valid_until"] > self.clock.now()]

    def ask_owner(self, claim, question):
        with self._tx() as state:
            self._checked(state, claim)
            if question.owner not in state["snapshot"]["members"] or question.snapshot_ref != claim.snapshot_ref:
                raise ContractError("NOT_AUTHORIZED")
            old = state["questions"].get(question.id)
            if old:
                if {k: v for k, v in old.items() if k != "answer"} != {k: v for k, v in primitive(question).items() if k != "answer"}:
                    raise ContractError("KEY_BODY_MISMATCH")
            else:
                state["questions"][question.id] = primitive(question)
            return question.id

    def get_question(self, claim, question_id):
        with self._tx() as state:
            self._checked(state, claim)
            question = Question.model_validate(state["questions"][question_id])
            if question.snapshot_ref != claim.snapshot_ref:
                raise ContractError("QUESTION_SUPERSEDED")
            return question

    def _has_grant(self, state, owner, kind, action_ref):
        key = f"{owner}:{kind}:{action_ref}"
        return state["grants"].get(key) == [state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]

    def has_permission(self, claim, owner, kind, action_ref):
        with self._tx() as state:
            self._checked(state, claim)
            return self._has_grant(state, owner, kind, action_ref)

    def answer_question(self, owner, question_id, version, answer, request_key):
        if not isinstance(answer, bool):
            raise ContractError("INVALID_ANSWER")
        with self._tx() as state:
            question = state["questions"][question_id]
            if owner != question["owner"] or owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            body = dict(question=question_id, version=version, answer=answer)
            key = f"answer:{owner}:{request_key}"
            old = self._receipt(state, key, body)
            if old:
                return old["result"]
            if version != question["version"] or question["snapshot_ref"] != digest(state["snapshot"]):
                raise ContractError("QUESTION_SUPERSEDED")
            if question["answer"] is not None:
                raise ContractError("ALREADY_ANSWERED")
            question["answer"] = answer
            # Disclosure/consultation answers are action grants. They invalidate
            # old execution authority, never create final proposal consent.
            if answer and question["kind"] in {"DISCLOSURE", "CONSULTATION", "EXPLORE", "PARTICIPATION"}:
                if question["kind"] == "PARTICIPATION":
                    prefs = state["briefs"][owner]["preferences"]
                    prefs.update(confirmed=True, revision=prefs["revision"] + 1)
                else:
                    state["grants"][f"{owner}:{question['kind']}:{question['action_ref']}"] = [state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]
                self._supersede(state, "permission_revision")
            for job in state["jobs"].values():
                if job["status"] == "WAITING_FOR_INPUT" and self._answers_ready(state, job):
                    job["status"] = "QUEUED"
            return self._record(state, key, body, question_id)

    def _answers_ready(self, state, job):
        return bool(job["questions"]) and all(state["questions"][q]["answer"] is not None for q in job["questions"])

    def park_job(self, claim, question_refs):
        with self._tx() as state:
            job = self._checked(state, claim)
            if not question_refs or any(q not in state["questions"] or state["questions"][q]["snapshot_ref"] != claim.snapshot_ref for q in question_refs):
                raise ContractError("QUESTION_SUPERSEDED")
            job["questions"] = question_refs
            job.update(status="QUEUED" if self._answers_ready(state, job) else "WAITING_FOR_INPUT", token=None, expiry=0)
            self._close_threads(state, claim.generation, "NEEDS_OWNER_INPUT")

    def schedule_resume(self, job_id):
        with self._tx() as state:
            job = state["jobs"][job_id]
            ready = job["snapshot_ref"] == digest(state["snapshot"]) and self._answers_ready(state, job)
            if ready and job["status"] == "WAITING_FOR_INPUT":
                job["status"] = "QUEUED"
            return ready

    def _close_threads(self, state, generation, reason):
        for thread in state["threads"].values():
            if thread["generation"] == generation and thread["state"] == "OPEN":
                thread.update(state="CLOSED", reason=reason)

    def close_threads(self, claim, reason):
        with self._tx() as state:
            self._checked(state, claim)
            self._close_threads(state, claim.generation, reason)

    def publish_proposal(self, claim, selected, candidates, report, key):
        with self._tx() as state:
            # A lost successful response can be reconciled after lease release.
            body = dict(selected=list(selected), report=primitive(report))
            old = self._receipt(state, f"publish:{claim.job_id}:{key}", body)
            if old:
                if state["jobs"][claim.job_id]["worker"] != claim.worker_id or state["jobs"][claim.job_id]["token"] != claim.attempt_token or digest(state["snapshot"]) != claim.snapshot_ref:
                    raise ContractError("STALE_SNAPSHOT")
                return old["result"]
            job = self._checked(state, claim)
            snapshot = Snapshot.model_validate(state["snapshot"])
            records = [Candidate.model_validate(state["candidates"][ref]) for ref in selected]
            if not report.valid or report.snapshot_ref != claim.snapshot_ref or report.selected != tuple(selected) or report.candidate_digest != digest([primitive(c) for c in candidates]):
                raise ContractError("INVALID_VALIDATION_REPORT")
            if len(records) != len(snapshot.members) or {r.owner for r in records} != set(snapshot.members):
                raise ContractError("INVALID_COVERAGE")
            for c in records:
                brief = Brief.model_validate(state["briefs"][c.owner])
                if c.total_cents is None or c.total_cents > brief.hard_cap_cents or c.observed_at > self.clock.now() or c.valid_until <= self.clock.now() or (brief.baggage_required and c.baggage_included is not True) or not brief.preferences.confirmed:
                    raise ContractError("INVALID_PROPOSAL")
            if max(c.arrival_epoch for c in records) - min(c.arrival_epoch for c in records) > snapshot.arrival_overlap_seconds:
                raise ContractError("INVALID_PROPOSAL")
            terms = {"scope": "FLIGHTS_ONLY", "selected": [primitive(r) for r in records]}
            proposal_id = f"proposal-{digest(terms)[:16]}-{claim.generation}"
            state["proposals"][proposal_id] = dict(terms=terms, terms_digest=digest(terms), snapshot_ref=claim.snapshot_ref,
                state="AWAITING_REVIEW", required_members=list(snapshot.members))
            job.update(status="SUCCEEDED", outcome=proposal_id, expiry=0)
            self._close_threads(state, claim.generation, "RESOLVED")
            return self._record(state, f"publish:{claim.job_id}:{key}", body, proposal_id)

    def finish_job(self, claim, outcome):
        with self._tx() as state:
            job = state["jobs"].get(claim.job_id, {})
            if job.get("status") == "PAUSED" and job.get("token") == claim.attempt_token and job.get("worker") == claim.worker_id and job.get("outcome") == outcome:
                return
            job = self._checked(state, claim)
            job.update(status="PAUSED", outcome=outcome, expiry=0)
            self._close_threads(state, claim.generation, outcome)

    def fail_job(self, claim, code):
        with self._tx() as state:
            job = state["jobs"].get(claim.job_id, {})
            if job.get("status") == "FAILED" and job.get("token") == claim.attempt_token and job.get("worker") == claim.worker_id and job.get("outcome") == code:
                return
            job = self._checked(state, claim)
            job.update(status="FAILED", outcome=code, expiry=0)
            self._close_threads(state, claim.generation, code)

    def _supersede(self, state, revision):
        state["snapshot"][revision] += 1
        for job in state["jobs"].values():
            if job["status"] not in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}:
                job.update(status="SUPERSEDED", token=None, expiry=0)
                self._close_threads(state, job["generation"], "SUPERSEDED")
        for proposal in state["proposals"].values():
            proposal["state"] = "STALE"
        return self._new_job(state)

    def update_preferences(self, owner, preferences):
        with self._tx() as state:
            if owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            old = state["briefs"][owner]["preferences"]
            prefs = preferences.model_copy(update={"revision": old["revision"] + 1})
            state["briefs"][owner]["preferences"] = primitive(prefs)
            state["grants"] = {k: v for k, v in state["grants"].items() if not k.startswith(owner + ":")}
            return self._supersede(state, "permission_revision")

    def update_cap(self, owner, cap):
        with self._tx() as state:
            brief = Brief.model_validate({**state["briefs"][owner], "hard_cap_cents": cap})
            state["briefs"][owner] = primitive(brief)
            return self._supersede(state, "input_revision")

    def cancel(self, job_id):
        with self._tx() as state:
            job = state["jobs"][job_id]
            job.update(status="CANCELLED", token=None, expiry=0)
            self._close_threads(state, job["generation"], "CANCELLED")

    def eligible_jobs(self):
        with self._tx() as state:
            return [j["id"] for j in state["jobs"].values() if j["snapshot_ref"] == digest(state["snapshot"]) and
                    (j["status"] == "QUEUED" or (j["status"] == "RUNNING" and j["expiry"] <= self.clock.now()))]

    def owner_questions(self, owner):
        with self._tx() as state:
            return [Question.model_validate(q) for q in state["questions"].values() if q["owner"] == owner and q["snapshot_ref"] == digest(state["snapshot"])]

    def shared_report(self):
        with self._tx() as state:
            return {"jobs": [{k: j.get(k) for k in ("id", "status", "outcome", "calls")} for j in state["jobs"].values()],
                "messages": [r["message"] for r in state["messages"].values() if r["message"]["audience"] == "PUBLIC_SUMMARY"],
                "threads": state["threads"], "proposals": state["proposals"], "consent_count": len(state["consents"])}
