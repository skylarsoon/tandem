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

from .contracts import (TOPICS, Brief, Candidate, Claim, ContractError, IntakeContext, IntakeMessage, Limits, Message,
                        Preferences, Question, Snapshot, TripBrief, ValidationReport, digest)
from .briefs import NO_CONFIRMED_CAP, apply_row
from .intake_policy import MAX_QUESTIONS, MONEY_KEYS
from .ports import Clock, SystemClock
from .solver import TripValidator


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
                         candidates={}, candidate_context={}, proposals={}, consents=[], grants={}, events=[], sequence=0,
                         notes={}, impacts={})
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
        # Onboarding jobs serve one person's conversation and outlive room revisions.
        if job.get("kind", "PLAN") == "PLAN" and digest(state["snapshot"]) != claim.snapshot_ref:
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
            kind = job.get("kind", "PLAN")
            if kind == "PLAN" and job["snapshot_ref"] != digest(state["snapshot"]):
                raise ContractError("STALE_SNAPSHOT")
            eligible = job["status"] == "QUEUED" or (job["status"] == "RUNNING" and job["expiry"] <= self.clock.now())
            if not eligible:
                raise ContractError("JOB_NOT_ELIGIBLE")
            job.update(status="RUNNING", worker=worker_id, token=uuid.uuid4().hex, expiry=self.clock.now() + self.limits.lease_seconds)
            claim = Claim(job_id=job_id, worker_id=worker_id, attempt_token=job["token"], generation=job["generation"],
                snapshot_ref=job["snapshot_ref"] or digest(state["snapshot"]), lease_expiry=job["expiry"], kind=kind,
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

    def read_rules(self, claim):
        with self._tx() as state:
            self._checked(state, claim)
            return self._room_rules(state)

    def read_profile(self, claim, owner):
        with self._tx() as state:
            self._checked(state, claim)
            if owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            answers = state.get("profiles", {}).get(owner, {})
            return {"summary": state.get("profile_summaries", {}).get(owner),
                    "answers": [{"question_id": q, "status": "ANSWERED", "value": v} for q, v in answers.items()]}

    def set_profile_summary(self, owner: str, summary: str) -> None:
        """The owner confirmed this summary of their profile ("Does this sound like you?")."""
        with self._tx() as state:
            state.setdefault("profile_summaries", {})[owner] = summary

    def _room_rules(self, state):
        """As the database keeps them: confirmed named limits through the shared conversion
        (hard and soft), plus rules confirmed as rules. Rooms seeded with Briefs directly
        (no onboarding rows) read their Briefs' limits."""
        from .briefs import rules_from_brief
        from .rules import Need, RoomRules, Rule, SearchFact, from_legacy
        rows = self._onboarding(state)["constraints"] if "onboarding" in state else {}
        items = []
        for o in state["snapshot"]["members"]:
            if rows.get(o):
                items += [x for key, row in rows[o].items() for x in from_legacy(o, key, row)]
            else:
                items += rules_from_brief(Brief.model_validate(state["briefs"][o]))
            items += [Rule.model_validate({**r, "owner": o}) for r in state.get("rules", {}).get(o, [])]
        ranking = {o: tuple((state.get("profiles", {}).get(o, {}).get("C03") or {}).get("ranked") or ())
                   for o in state["snapshot"]["members"]}
        return RoomRules(rules=tuple(x for x in items if isinstance(x, Rule)), needs=tuple(x for x in items if isinstance(x, Need)),
                         facts=tuple(x for x in items if isinstance(x, SearchFact)), ranking=ranking)

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
                if sorted(thread["participants"]) != sorted(participants) or thread["round"] != message.round_id:
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
                if candidate.owner is not None and candidate.owner not in state["snapshot"]["members"]:
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

    def declined_concession_owners(self, claim, draft_ref):
        with self._tx() as state:
            self._checked(state, claim)
            revisions = [state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]
            return tuple(owner for owner in state["snapshot"]["members"]
                if state.get("refusals", {}).get(f"{owner}:{draft_ref}") == revisions)

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
            if question.get("nudge"):
                # A just-in-time answer is the owner's confirmed constraint; the waiting
                # job resumes and re-plans with it (no restart, so others' questions stand).
                self._apply(state, owner, question["nudge"]["yes" if answer else "no"])
            if not answer and question["kind"] == "CONSULTATION":
                state.setdefault("refusals", {})[f"{owner}:{question['action_ref']}"] = [
                    state["snapshot"]["input_revision"], state["snapshot"]["membership_revision"]]
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

    def publish_proposal(self, claim, selected, candidates, report, key, explanation=None, burdens=None,
                         decision_id="trip", scope="COMPLETE_PLAN", stage="TRAVEL"):
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
            # The backend re-checks every hard rule from its own records, for what this proposal decides.
            if scope != "COMPLETE_PLAN":
                snapshot = snapshot.model_copy(update={"components": tuple(dict.fromkeys(r.category for r in records))})
            briefs = [Brief.model_validate(state["briefs"][o]) for o in snapshot.members]
            checked = TripValidator().validate(snapshot, briefs, records, tuple(selected), self.clock.now())
            self._check_rules(state, snapshot, records)
            if "INVALID_COVERAGE" in checked.checks:
                raise ContractError("INVALID_COVERAGE")
            if not checked.valid or not all(b.preferences.confirmed for b in briefs):
                raise ContractError("INVALID_PROPOSAL")
            terms = {"scope": "TRIP" if len(snapshot.components) > 1 else "FLIGHTS_ONLY", "selected": [primitive(r) for r in records]}
            proposal_id = f"proposal-{digest(terms)[:16]}-{claim.generation}"
            state["proposals"][proposal_id] = dict(terms=terms, terms_digest=digest(terms), snapshot_ref=claim.snapshot_ref,
                state="AWAITING_REVIEW", required_members=list(snapshot.members),
                summary=explanation.summary if explanation else None, decision_id=decision_id, scope=scope, stage=stage,
                burdens=dict(burdens or {}), generation=claim.generation)
            if explanation:
                if {n.impact.owner for n in explanation.impacts} - set(snapshot.members):
                    raise ContractError("INVALID_PROPOSAL")
                for note in explanation.impacts:
                    state["impacts"].setdefault(note.impact.owner, {})[proposal_id] = primitive(note)
            job.update(status="SUCCEEDED", outcome=proposal_id, expiry=0)
            self._close_threads(state, claim.generation, "RESOLVED")
            return self._record(state, f"publish:{claim.job_id}:{key}", body, proposal_id)

    def _check_rules(self, state, snapshot, records):
        """Like the database: each pick against its owner's rules (everyone's, for a shared pick)
        and the trip's dates. Unknown fails a person's rule; a pick without a trip-date fact isn't held to it."""
        from .rules import attributes, evaluate
        rules = self._room_rules(state).within_trip(snapshot.start_date, snapshot.end_date)
        for record in records:
            facts = record.model_dump(mode="json")
            owners = [record.owner] if record.owner else list(snapshot.members)
            for rule in rules.binding(None, record.category):
                if rule.owner is not None and rule.owner not in owners:
                    continue
                verdict = evaluate(rule, facts)
                unknown_ok = attributes()["categories"][record.category].get(rule.attribute, {}).get("unknown_ok")
                if verdict == "VIOLATED" or (verdict == "UNKNOWN" and rule.owner is not None and not unknown_ok):
                    raise ContractError("INVALID_PROPOSAL")

    # ---------- agreement (the database's twin) ----------

    def respond(self, owner: str, proposal_id: str, response: str = "ACCEPT") -> None:
        """An owner's answer to a proposal. When everyone affected accepts, it's agreed: a stage
        decision becomes a commitment and the next stage is planned."""
        with self._tx() as state:
            proposal = state["proposals"].get(proposal_id)
            if not proposal or owner not in proposal["required_members"]:
                raise ContractError("NOT_AUTHORIZED")
            if proposal["state"] not in ("AWAITING_REVIEW", "AGREED"):
                raise ContractError("STALE_SNAPSHOT")
            proposal.setdefault("responses", {})[owner] = response
            state["consents"].append({"proposal": proposal_id, "owner": owner, "response": response})
            agreed = all(proposal["responses"].get(m) == "ACCEPT" for m in proposal["required_members"])
            if agreed != (proposal["state"] == "AGREED"):
                proposal["state"] = "AGREED" if agreed else "AWAITING_REVIEW"
                self._commit(state)
                if agreed and proposal.get("scope", "COMPLETE_PLAN") != "COMPLETE_PLAN":
                    self._new_job(state)  # agreeing one stage queues planning of the next

    def _commit(self, state):
        """The snapshot's commitments and what they cost each person, from the agreed proposals."""
        from .contracts import Commitment
        from .rules import committed_costs
        agreed = [p for p in state["proposals"].values() if p["state"] == "AGREED"]
        commitments = [Commitment(decision_id=p.get("decision_id", "trip"), scope=p.get("scope", "COMPLETE_PLAN"),
                                  refs=tuple(c["id"] for c in p["terms"]["selected"])) for p in agreed]
        facts = {c["id"]: c for p in agreed for c in p["terms"]["selected"]}
        state["snapshot"]["commitments"] = [primitive(c) for c in commitments]
        state["snapshot"]["committed_scaled"] = committed_costs(commitments, facts, lambda f: f.get("owner"),
                                                                tuple(state["snapshot"]["members"]))

    def _reopen_broken(self, state):
        """An agreed decision a confirmed change now breaks (a pick against someone's rules, or
        their whole-trip limit with everything agreed) is reopened, never kept silently."""
        snapshot = Snapshot.model_validate(state["snapshot"])
        n = len(snapshot.members)
        caps = {r.owner: r.value for r in self._room_rules(state).binding() if r.aggregate == "my_cost" and r.operator == "lte"}
        for proposal in sorted((p for p in state["proposals"].values() if p["state"] == "AGREED"),
                               key=lambda p: p.get("generation", ""), reverse=True):
            records = [Candidate.model_validate(c) for c in proposal["terms"]["selected"]]
            try:
                self._check_rules(state, snapshot, records)
                broken = any(cost > n * caps[m] for m, cost in snapshot.committed_scaled.items() if m in caps)
            except ContractError:
                broken = True
            if broken:
                proposal["state"] = "REOPENED"
                self._commit(state)
                snapshot = Snapshot.model_validate(state["snapshot"])

    def notify_owner(self, claim, owner, text, key):
        with self._tx() as state:
            self._checked(state, claim)
            if owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            note_key = f"{claim.generation}:{key}"
            if note_key not in state["notes"].setdefault(owner, {}):
                # The agent's side of the owner's private conversation.
                self._message(state, owner, "AGENT", text)
            state["notes"][owner][note_key] = text

    def set_agent_activity(self, claim, owner, activity):
        with self._tx() as state:
            self._checked(state, claim)
            if owner != "mediator" and owner not in state["snapshot"]["members"]:
                raise ContractError("NOT_AUTHORIZED")
            state.setdefault("activities", {})[owner] = activity

    def room_event(self, claim, kind, payload, key):
        with self._tx() as state:
            self._checked(state, claim)
            reasons = [b["private_reason"] for b in state["briefs"].values() if b["private_reason"]]
            if any(r in json.dumps(payload) for r in reasons):
                raise ContractError("PRIVATE_CONTENT_DETECTED")
            event = dict(key=f"{claim.generation}:{key}", kind=kind, payload=payload)
            if event not in state["events"]:
                state["events"].append(event)

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
            if job.get("kind", "PLAN") == "PLAN" and job["status"] not in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}:
                job.update(status="SUPERSEDED", token=None, expiry=0)
                self._close_threads(state, job["generation"], "SUPERSEDED")
        for proposal in state["proposals"].values():
            if proposal["state"] != "AGREED":  # agreed decisions stand; pending ones are out of date
                proposal["state"] = "STALE"
        self._reopen_broken(state)
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
            return [j["id"] for j in state["jobs"].values()
                    if (j.get("kind", "PLAN") != "PLAN" or j["snapshot_ref"] == digest(state["snapshot"])) and
                    (j["status"] == "QUEUED" or (j["status"] == "RUNNING" and j["expiry"] <= self.clock.now()))]

    def current_snapshot(self):
        """Local harness inspection only; production uses authorized job reads."""
        with self._tx() as state:
            return Snapshot.model_validate(state["snapshot"])

    def owner_questions(self, owner):
        with self._tx() as state:
            return [Question.model_validate(q) for q in state["questions"].values() if q["owner"] == owner and q["snapshot_ref"] == digest(state["snapshot"])]

    def owner_view(self, owner):
        """Local harness inspection of what one owner privately sees."""
        with self._tx() as state:
            return {"notes": list(state["notes"].get(owner, {}).values()), "impacts": state["impacts"].get(owner, {})}

    def shared_report(self):
        with self._tx() as state:
            return {"jobs": [{k: j.get(k) for k in ("id", "status", "outcome", "calls")} for j in state["jobs"].values()],
                "messages": [r["message"] for r in sorted(state["messages"].values(), key=lambda r: r["sequence"])
                    if r["message"]["audience"] == "PUBLIC_SUMMARY"],
                "threads": state["threads"], "proposals": state["proposals"], "consent_count": len(state["consents"]),
                "events": state["events"]}

    # ---------- onboarding (local harness of the module's intake flow) ----------

    def seed_trip(self, members: tuple[str, ...], organizer: str | None = None) -> None:
        """A room with people but no limits yet: they arrive through onboarding."""
        with self._tx() as state:
            if state:
                raise ContractError("ALREADY_SEEDED")
            snapshot = Snapshot(room_id="fixture-room", members=tuple(members))
            state.update(snapshot=primitive(snapshot), jobs={}, receipts={}, messages={}, threads={}, questions={},
                operations={}, candidates={}, candidate_context={}, proposals={}, consents=[], grants={}, events=[],
                sequence=0, notes={}, impacts={},
                briefs={o: primitive(Brief(owner=o, hard_cap_cents=NO_CONFIRMED_CAP)) for o in members})
            self._onboarding(state)["organizer"] = organizer or members[0]

    def set_profile(self, owner: str, answers: dict) -> None:
        """The owner's persistent profile answers (question id -> value), as the
        database's job context carries them."""
        with self._tx() as state:
            state.setdefault("profiles", {})[owner] = answers

    @staticmethod
    def _onboarding(state):
        return state.setdefault("onboarding", {"organizer": state["snapshot"]["members"][0], "trip_brief": None,
            "intakes": {}, "pmessages": [], "patches": [], "constraints": {}, "job_seq": 0})

    def _message(self, state, owner, sender, body):
        board = self._onboarding(state)
        board["pmessages"].append({"id": len(board["pmessages"]) + 1, "owner": owner, "sender": sender, "body": body})
        return board["pmessages"][-1]["id"]

    def _agent_job(self, state, kind, owner, purpose):
        board = self._onboarding(state)
        board["job_seq"] += 1
        job_id = f"{kind.lower()}-{board['job_seq']}"
        state["jobs"][job_id] = dict(id=job_id, kind=kind, subject=owner, purpose=purpose, generation=job_id,
            snapshot_ref=None, status="QUEUED", token=None, worker=None, expiry=0, questions=[],
            calls={"model": 0, "research": 0})
        return job_id

    # Human side (what the app's screens call).

    def describe_trip(self, description: str) -> str:
        with self._tx() as state:
            board = self._onboarding(state)
            old = board["trip_brief"] or {"revision": 0, "brief": None}
            board["trip_brief"] = {"description": description, "brief": old["brief"], "unclear": [], "state": "DRAFT",
                                   "revision": old["revision"] + 1}
            return self._agent_job(state, "INTAKE", board["organizer"], "BRIEF")

    def confirm_trip_brief(self) -> None:
        with self._tx() as state:
            board = self._onboarding(state)
            brief = board["trip_brief"]
            if not brief or brief["state"] != "PROPOSED":
                raise ContractError("INVALID_STATE")
            brief["state"] = "CONFIRMED"
            state["snapshot"]["components"] = list(brief["brief"]["components"])
            state["snapshot"]["onboarding"] = True
            facts = brief["brief"]
            state["snapshot"].update(destination=facts["destination"], destination_airport=facts["destination_airport"],
                                     start_date=facts["start_date"], end_date=facts["end_date"])
            # As the database's settings: the organizer's order, and arrivals held together only if required.
            together = facts.get("arrive_together") or state["snapshot"].get("arrive_together", "required")
            state["snapshot"].update(arrive_together=together, stages=list(facts.get("stages") or state["snapshot"].get("stages") or []),
                                     activities=facts.get("activities"))
            if together != "required":
                from .adapters.spacetime import NO_ARRIVAL_LIMIT
                state["snapshot"]["arrival_overlap_seconds"] = NO_ARRIVAL_LIMIT
            for owner in state["snapshot"]["members"]:
                if owner not in board["intakes"]:
                    board["intakes"][owner] = {"state": "ASKING", "coverage": self._coverage(state, owner), "question_count": 0}
                    self._agent_job(state, "INTAKE", owner, "INTAKE")
            self._supersede(state, "input_revision")

    def _coverage(self, state, owner):
        keys = set(self._onboarding(state)["constraints"].get(owner, {}))
        confirmed = state["briefs"][owner]["preferences"]["confirmed"]
        return {"origin": "ANSWERED" if "origin_airport" in keys else "OPEN", "dates": "OPEN",
                "money": "ANSWERED" if keys & MONEY_KEYS else "OPEN", "settings": "ANSWERED" if confirmed else "OPEN"}

    def say(self, owner: str, body: str) -> str:
        """The owner writes to their own agent; an INTERPRET job reads it."""
        with self._tx() as state:
            self._message(state, owner, "HUMAN", body)
            return self._agent_job(state, "INTERPRET", owner, "MESSAGE")

    def _apply(self, state, owner, patch):
        if "rule" in patch:  # any checkable limit, kept as a rule with their words
            mine = state.setdefault("rules", {}).setdefault(owner, [])
            slot = lambda r: (r.get("category"), r.get("attribute"), r.get("aggregate"), r["operator"], r.get("kind", "hard"))  # noqa: E731
            mine[:] = [r for r in mine if slot(r) != slot(patch["rule"])]  # a later statement replaces it
            mine.append({**patch["rule"], "source_quote": patch.get("source_quote") or ""})
            return
        row = {k: patch[k] for k in ("value", "hardness") if k in patch}
        row["source_quote"] = patch.get("source_quote")
        row["authorized_range"] = patch.get("authorized_range")
        row["private_rationale"] = patch.get("private_rationale")
        self._onboarding(state)["constraints"].setdefault(owner, {})[patch["key"]] = row
        state["briefs"][owner] = primitive(apply_row(Brief.model_validate(state["briefs"][owner]), patch["key"], row))

    @staticmethod
    def _has_money_rule(state, owner):
        from .rules import Rule, attribute_type
        return any(attribute_type(Rule.model_validate(r)) == "money" for r in state.get("rules", {}).get(owner, []))

    def confirm_origin(self, owner: str, patch_id: int) -> None:
        with self._tx() as state:
            board = self._onboarding(state)
            patch = next((p for p in board["patches"] if p["id"] == patch_id and p["owner"] == owner), None)
            if not patch or patch["state"] != "PENDING" or patch["patch"]["key"] != "origin_airport":
                raise ContractError("INVALID_STATE")
            patch["state"] = "CONFIRMED"
            self._apply(state, owner, patch["patch"])
            board["intakes"][owner]["coverage"]["origin"] = "ANSWERED"
            self._supersede(state, "input_revision")

    def confirm_patch(self, owner: str, patch_id: int) -> None:
        """One change after onboarding ("my budget is now $900"), confirmed by its owner: replan."""
        with self._tx() as state:
            board = self._onboarding(state)
            patch = next((p for p in board["patches"] if p["id"] == patch_id and p["owner"] == owner), None)
            if not patch or patch["state"] != "PENDING":
                raise ContractError("INVALID_STATE")
            patch["state"] = "CONFIRMED"
            self._apply(state, owner, patch["patch"])
            self._supersede(state, "input_revision")

    def confirm_intake(self, owner: str, accepted: list[int], settings: dict | None = None) -> None:
        with self._tx() as state:
            board = self._onboarding(state)
            intake = board["intakes"].get(owner)
            if not intake or intake["state"] != "SUMMARY":
                raise ContractError("INVALID_STATE")
            for patch in board["patches"]:
                if patch["owner"] == owner and patch["state"] == "PENDING":
                    patch["state"] = "CONFIRMED" if patch["id"] in accepted else "REJECTED"
                    if patch["state"] == "CONFIRMED":
                        self._apply(state, owner, patch["patch"])
            prefs = state["briefs"][owner]["preferences"]
            if settings is not None:
                prefs.update(settings, confirmed=True, revision=prefs["revision"] + 1)
            if not prefs["confirmed"]:
                raise ContractError("PREFERENCES_UNCONFIRMED")
            keys = set(board["constraints"].get(owner, {}))
            if (not keys & MONEY_KEYS and not self._has_money_rule(state, owner)) or "origin_airport" not in keys:
                raise ContractError("INTAKE_INCOMPLETE")
            intake["state"] = "CONFIRMED"
            state["snapshot"]["onboarded"] = sorted({*state["snapshot"].get("onboarded", []), owner})
            self._supersede(state, "input_revision")

    def conversation(self, owner: str) -> list[dict]:
        with self._tx() as state:
            return [m for m in self._onboarding(state)["pmessages"] if m["owner"] == owner]

    def patches_of(self, owner: str) -> list[dict]:
        with self._tx() as state:
            return [p for p in self._onboarding(state)["patches"] if p["owner"] == owner]

    def intake_of(self, owner: str) -> dict | None:
        with self._tx() as state:
            return self._onboarding(state)["intakes"].get(owner)

    def trip_brief(self) -> dict | None:
        with self._tx() as state:
            return self._onboarding(state)["trip_brief"]

    def brief_of(self, owner: str) -> Brief:
        with self._tx() as state:
            return Brief.model_validate(state["briefs"][owner])

    # Worker side (what the intake engine calls).

    def load_intake(self, claim):
        with self._tx() as state:
            job = self._checked(state, claim)
            board = self._onboarding(state)
            owner, members = job["subject"], state["snapshot"]["members"]
            trip = board["trip_brief"] or {}
            intake = board["intakes"].get(owner) or {}
            return IntakeContext(member_id=members.index(owner) + 1, owner=owner, purpose=job["purpose"],
                is_organizer=owner == board["organizer"], others=tuple(m for m in members if m != owner),
                description=trip.get("description", ""), brief=trip.get("brief") if trip.get("state") == "CONFIRMED" else None,
                brief_state=trip.get("state"),
                messages=tuple(IntakeMessage(id=m["id"], sender=m["sender"], body=m["body"]) for m in board["pmessages"] if m["owner"] == owner),
                pending=tuple(p["patch"] for p in board["patches"] if p["owner"] == owner and p["state"] == "PENDING"),
                confirmed_keys=tuple(board["constraints"].get(owner, {})), intake_state=intake.get("state"),
                coverage=intake.get("coverage", {}), question_count=intake.get("question_count", 0),
                preferences_confirmed=state["briefs"][owner]["preferences"]["confirmed"],
                profile=state.get("profiles", {}).get(owner, {}))

    def propose_patch(self, claim, owner, patch, source_message_id, key):
        with self._tx() as state:
            self._checked(state, claim)
            board = self._onboarding(state)
            if self._receipt(state, f"patch:{claim.job_id}:{key}", patch):
                return
            quote = patch.get("source_quote")
            if quote is not None:
                source = next((m for m in board["pmessages"] if m["id"] == source_message_id), None)
                if not source or source["owner"] != owner or source["sender"] != "HUMAN" or quote not in source["body"]:
                    raise ContractError("INVALID_EVIDENCE_REF")
            if patch.get("source_profile") is not None and patch["source_profile"] not in state.get("profiles", {}).get(owner, {}):
                raise ContractError("INVALID_EVIDENCE_REF")  # only a question they answered in their profile
            board["patches"].append({"id": len(board["patches"]) + 1, "owner": owner, "patch": patch,
                                     "source_message_id": source_message_id, "state": "PENDING"})
            self._record(state, f"patch:{claim.job_id}:{key}", patch, None)

    def withdraw_patches(self, claim, owner, key):
        with self._tx() as state:
            self._checked(state, claim)
            for patch in self._onboarding(state)["patches"]:
                if patch["owner"] == owner and patch["state"] == "PENDING":
                    patch["state"] = "REJECTED"

    def update_intake(self, claim, owner, coverage, state_name, asked, key):
        with self._tx() as state:
            self._checked(state, claim)
            if self._receipt(state, f"intake:{claim.job_id}:{key}", [coverage, state_name, asked]):
                return
            intake = self._onboarding(state)["intakes"][owner]
            if intake["state"] == "CONFIRMED" or state_name not in ("ASKING", "SUMMARY") or set(coverage) != set(TOPICS):
                raise ContractError("INVALID_STATE")
            count = intake["question_count"] + (1 if asked else 0)
            if count > MAX_QUESTIONS:
                raise ContractError("TURN_BUDGET_EXHAUSTED")
            intake.update(state=state_name, coverage=dict(coverage), question_count=count)
            self._record(state, f"intake:{claim.job_id}:{key}", [coverage, state_name, asked], None)

    def propose_preferences(self, claim, owner, speak_up, ask_first, give_time, key):
        with self._tx() as state:
            self._checked(state, claim)
            prefs = state["briefs"][owner]["preferences"]
            if prefs["confirmed"]:
                raise ContractError("INVALID_STATE")
            prefs.update(speak_up_for_me=speak_up, ask_me_first=ask_first, give_me_time=give_time)

    def propose_trip_brief(self, claim, brief, unclear, key):
        with self._tx() as state:
            self._checked(state, claim)
            trip = self._onboarding(state)["trip_brief"]
            if not trip or trip["state"] == "CONFIRMED":
                raise ContractError("INVALID_STATE")
            trip.update(brief=primitive(TripBrief.model_validate(primitive(brief))), unclear=list(unclear), state="PROPOSED",
                        revision=trip["revision"] + 1)
