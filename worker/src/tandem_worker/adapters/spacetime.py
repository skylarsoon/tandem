"""BackendPort over the real SpacetimeDB module (contracts/backend.md).

Talks HTTP: reducers/procedures via /call, reads via per-caller views and the
worker-only execution context. Owner and agent names are translated between the
worker's strings ("mediator", owner names) and the module's numeric ids.
Natural-language checks stay in the worker; the module enforces state, fencing,
audience, preference gates and the validation digest.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from ..contracts import (Brief, Candidate, Claim, ContractError, IntakeContext, IntakeMessage, Limits, Message, Preferences,
                         Question, Snapshot, TripBrief, ValidationReport, digest)
from ..ports import Clock, SystemClock
from ..briefs import brief_from_constraints
from ..solver import POLICY

# Worker question kinds ↔ module question kinds.
KINDS = {
    "PARTICIPATION": "PARTICIPATION_CONFIRMATION",
    "DISCLOSURE": "OBJECTION_DISCLOSURE",
    "CONSULTATION": "CONCESSION_CONSULTATION",
    "EXPLORE": "BROADER_EXPLORATION",
    "MISSING_FACT": "MISSING_FACT",
}
WORKER_KINDS = {v: k for k, v in KINDS.items()}
AUDIENCES = {"PUBLIC_SUMMARY": "ROOM", "OWNER_PRIVATE": "OWNER_PRIVATE", "INTERNAL": "INTERNAL"}
# Fixed prompts: question records never carry private reasons or caps.
PROMPTS = {
    "PARTICIPATION": "Please confirm how your agent should represent you.",
    "DISCLOSURE": "May your agent tell the group this option doesn't meet your requirements, without saying why?",
    "CONSULTATION": "Your agent would like to agree to a concession on your behalf. Is that OK?",
    "EXPLORE": "May your agent search beyond the range you allowed?",
    "MISSING_FACT": "Your agent needs one more detail before continuing.",
}
JOB_CLAIM_ERRORS = {"INVALID_STATE": "JOB_NOT_ELIGIBLE", "NOT_FOUND": "JOB_NOT_ELIGIBLE"}


def primitive(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


def some(value):
    return None if value is None else {"some": value}


class StdbHttp:
    """Minimal HTTP client. Top-level argument names are sent camelCase (as the
    module declares them); nested objects and view columns are snake_case."""

    def __init__(self, url: str, database: str, timeout: float = 30):
        self.url, self.database, self.timeout = url.rstrip("/"), database, timeout

    def _post(self, path: str, body: bytes | None, token: str | None, content_type: str) -> tuple[int, str]:
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    @staticmethod
    def _args(args: dict) -> bytes:
        camel = lambda name: re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), name)  # noqa: E731
        return json.dumps({camel(k): v for k, v in args.items()}).encode()

    def call(self, token: str, reducer: str, **args) -> None:
        status, body = self._post(f"/v1/database/{self.database}/call/{reducer}", self._args(args), token, "application/json")
        if status == 200:
            return
        code = body.partition(": ")[0]
        if status == 530 and re.fullmatch(r"[A-Z_]+", code):
            raise ContractError(code)
        if status == 504:
            raise ContractError("OUTCOME_UNKNOWN")
        raise RuntimeError(f"SpacetimeDB {reducer} failed with HTTP {status}")

    def procedure(self, token: str, name: str, **args) -> dict:
        status, body = self._post(f"/v1/database/{self.database}/call/{name}", self._args(args), token, "application/json")
        if status == 504:
            raise ContractError("OUTCOME_UNKNOWN")
        if status != 200:
            raise RuntimeError(f"SpacetimeDB {name} failed with HTTP {status}")
        result = json.loads(json.loads(body))
        if not result.get("ok"):
            raise ContractError(result.get("code", "UNKNOWN"))
        return result

    def view(self, token: str, name: str) -> list[dict]:
        status, body = self._post(f"/v1/database/{self.database}/sql", f"SELECT * FROM {name}".encode(), token, "text/plain")
        if status != 200:
            raise RuntimeError(f"SpacetimeDB read of {name} failed with HTTP {status}")
        rows = []
        for statement in json.loads(body):
            elements = statement["schema"]["elements"]
            for row in statement["rows"]:
                rows.append({e["name"]["some"]: _decode(v, e["algebraic_type"]) for e, v in zip(elements, row)})
        return rows

    def mint_identity(self) -> dict:
        status, body = self._post("/v1/identity", None, None, "application/json")
        if status != 200:
            raise RuntimeError(f"identity creation failed with HTTP {status}")
        return json.loads(body)


def _decode(value, typ):
    if "Product" in typ:
        elements = typ["Product"]["elements"]
        if len(elements) == 1 and elements[0]["name"].get("some") in {"__identity__", "__timestamp_micros_since_unix_epoch__"}:
            return value[0]
        return {e["name"]["some"]: _decode(v, e["algebraic_type"]) for e, v in zip(elements, value)}
    if "Sum" in typ:
        tag, payload = value
        variant = typ["Sum"]["variants"][tag]
        if [v["name"].get("some") for v in typ["Sum"]["variants"]] == ["some", "none"]:
            return None if variant["name"]["some"] == "none" else _decode(payload, variant["algebraic_type"])
        return variant["name"]["some"]
    if "Array" in typ:
        return [_decode(v, typ["Array"]) for v in value]
    return value


class SpacetimeBackend:
    """Implements ports.BackendPort against the deployed module.

    `owner_name` maps a context member to the worker's owner string (default
    "m<member_id>"). `owner_tokens` (owner → token) lets fixtures and demos
    answer questions as those humans; production answers come from the app.
    `room_id` limits eligible_jobs() to one room.
    """

    def __init__(self, url: str, database: str, worker_token: str, *, clock: Clock | None = None,
                 limits: Limits | None = None, room_id: int | None = None,
                 owner_name: Callable[[dict], str] | None = None, owner_tokens: dict[str, str] | None = None):
        self.http = StdbHttp(url, database)
        self.token = worker_token
        self.clock = clock or SystemClock()
        self.limits = limits or Limits()
        self.room_id = room_id
        self.owner_name = owner_name or (lambda member: f"m{member['member_id']}")
        self.owner_tokens = owner_tokens or {}

    # ---------- context and name translation ----------

    def _attempt(self, claim: Claim) -> dict:
        return {"job_id": int(claim.job_id), "attempt": int(claim.attempt_token)}

    def _context(self, claim: Claim) -> dict:
        return self.http.procedure(self.token, "load_execution_context", **self._attempt(claim))

    def _members(self, ctx: dict) -> dict[str, dict]:
        return {self.owner_name(m): m for m in ctx["members"] if m["active"]}

    def _agent(self, ctx: dict, name: str) -> int:
        if name == "mediator":
            return ctx["mediator_agent_id"]
        member = self._members(ctx).get(name)
        if member is None:
            raise ContractError("NOT_AUTHORIZED")
        return member["agent_id"]

    def _snapshot(self, ctx: dict) -> Snapshot:
        job, settings = ctx["job"], ctx["room"].get("settings") or {}
        return Snapshot(room_id=str(job["room_id"]), input_revision=job["input_revision"],
            membership_revision=job["membership_revision"], permission_revision=job["permission_revision"],
            members=tuple(self._members(ctx)), arrival_overlap_seconds=settings.get("arrival_overlap_seconds", 7200),
            components=tuple(settings.get("components", ("FLIGHT",))), people_per_bedroom=settings.get("people_per_bedroom", 2))

    def _claim(self, job: dict, worker_id: str) -> Claim:
        # snapshot_ref = digest(Snapshot), exactly what FlightValidator stamps on its report.
        snapshot = self._snapshot(self.http.procedure(self.token, "load_execution_context",
                                                      job_id=job["job_id"], attempt=job["attempt"]))
        return Claim(job_id=str(job["job_id"]), worker_id=worker_id, attempt_token=str(job["attempt"]), kind=job["kind"],
            generation=str(job["generation"]), snapshot_ref=digest(snapshot),
            graph_thread_ref=job["graph_thread_ref"] or f"graph-v1:room:{job['room_id']}:generation:{job['generation']}:coordinator",
            lease_expiry=int(job["lease_expires_at_micros"]) / 1_000_000)

    # ---------- claims and leases ----------

    def claim_job(self, job_id, worker_id, request_key):
        # Receipt keys are global in the module; scope them like the worker's
        # per-room store does.
        scope = f"room{self.room_id}:" if self.room_id is not None else ""
        try:
            job = self.http.procedure(self.token, "claim_job", job_id=some(int(job_id)),
                lease_seconds=int(self.limits.lease_seconds), request_key=f"{scope}{worker_id}:{request_key}")
        except ContractError as exc:
            raise ContractError(JOB_CLAIM_ERRORS.get(exc.code, exc.code)) from None
        return self._claim(job, worker_id)

    def check(self, claim):
        self.http.procedure(self.token, "check_lease", **self._attempt(claim))

    def renew_lease(self, claim):
        self.http.call(self.token, "renew_lease", lease_seconds=int(self.limits.lease_seconds), **self._attempt(claim))

    def pending_jobs(self) -> list[dict]:
        jobs = self.http.view(self.token, "worker_pending_jobs")
        return [j for j in sorted(jobs, key=lambda j: j["id"]) if self.room_id is None or j["room_id"] == self.room_id]

    def eligible_jobs(self):
        return [str(j["id"]) for j in self.pending_jobs()]

    def schedule_resume(self, job_id):
        return str(job_id) in self.eligible_jobs()

    # ---------- reads ----------

    def load_execution_context(self, claim):
        return self._snapshot(self._context(claim))

    def read_brief(self, claim, owner):
        ctx = self._context(claim)
        member = self._members(ctx).get(owner)
        if member is None:
            raise ContractError("NOT_AUTHORIZED")
        mine = {c["key"]: c for c in ctx["constraints"] if c["member_id"] == member["member_id"]}
        prefs = member["participation_preferences"]
        return brief_from_constraints(owner, mine, Preferences(speak_up_for_me=prefs["speak_up_for_me"],
            ask_me_first=prefs["ask_me_first"], give_me_time=prefs["give_me_time"], confirmed=prefs["confirmed"],
            revision=prefs["revision"]), prefs["reminders_opted_in"])

    # ---------- receipt-backed operations ----------

    def operation(self, claim, key, request, kind, function):
        limit = {"model": self.limits.model_calls, "research": self.limits.research_calls}.get(kind, 0)
        begun = self.http.procedure(self.token, "begin_operation", op_key=key, request_digest=digest(request),
            kind=kind, limit=limit, **self._attempt(claim))
        if begun["status"] == "DONE":
            return json.loads(begun["result_json"])
        result = primitive(function())
        self.http.call(self.token, "complete_operation", op_key=key, result_json=json.dumps(result, sort_keys=True),
            **self._attempt(claim))
        return result

    # ---------- evidence ----------

    def store_candidates(self, claim, candidates):
        ctx = self._context(claim)
        members = self._members(ctx)
        existing = {c["candidate_ref"]: c["facts"] for c in ctx["candidates"]}
        rows = []
        for candidate in candidates:
            candidate = Candidate.model_validate(primitive(candidate))
            if candidate.owner is not None and candidate.owner not in members:
                raise ContractError("NOT_AUTHORIZED")
            facts = {**candidate.model_dump(mode="json"), "total_cents": candidate.total_cents}
            if candidate.owner is not None:
                facts["owner_member_id"] = members[candidate.owner]["member_id"]
            old = existing.get(candidate.id)
            if old is not None and old != facts:
                raise ContractError("CANDIDATE_ID_CONFLICT")
            rows.append({"candidate_ref": candidate.id, "candidate_set_version": ctx["job"]["input_revision"],
                "category": candidate.category, "mode": candidate.mode, "source_url": candidate.source_url,
                "observed_at": datetime.fromtimestamp(candidate.observed_at, timezone.utc).isoformat(),
                "facts_json": json.dumps(facts, sort_keys=True),
                "missing_fields": (["baggage"] if candidate.category == "FLIGHT" and candidate.baggage_included is None
                                   else list(candidate.missing_critical_fields))})
        if rows:
            self.http.call(self.token, "record_candidates", request_key=f"{claim.job_id}:candidates:{digest(rows)}",
                candidates=rows, **self._attempt(claim))

    def reusable_candidates(self, claim):
        ctx = self._context(claim)
        job, now = ctx["job"], self.clock.now()
        result = []
        for c in ctx["candidates"]:
            if (c["input_revision"], c["membership_revision"]) != (job["input_revision"], job["membership_revision"]):
                continue
            facts = {k: v for k, v in c["facts"].items() if k != "owner_member_id"}
            if facts.get("valid_until", 0) > now:
                result.append(Candidate.model_validate(facts))
        return result

    # ---------- questions and grants ----------

    def ask_owner(self, claim, question):
        ctx = self._context(claim)
        member = self._members(ctx).get(question.owner)
        if member is None or question.snapshot_ref != claim.snapshot_ref:
            raise ContractError("NOT_AUTHORIZED")
        self.http.call(self.token, "ask_owner", request_key=question.id, member_id=member["member_id"],
            kind=KINDS[question.kind], action_json=json.dumps({"action_ref": question.action_ref}),
            prompt=question.prompt or PROMPTS[question.kind], answer_schema_json=json.dumps({"type": "boolean"}), thread_id=None,
            **self._attempt(claim))
        return question.id

    @staticmethod
    def _answer(kind: str, answer: dict | None) -> bool | None:
        if answer is None:
            return None
        if kind in {"DISCLOSURE", "CONSULTATION", "EXPLORE"}:
            return answer.get("granted")
        if kind == "PARTICIPATION":
            return answer.get("confirmed", True)
        return answer.get("answer", True)

    def get_question(self, claim, question_id):
        ctx = self._context(claim)
        row = next((q for q in ctx["questions"] if q["request_key"] == question_id), None)
        if row is None:
            raise KeyError(question_id)
        if row["state"] == "SUPERSEDED":
            raise ContractError("QUESTION_SUPERSEDED")
        owner = next(name for name, m in self._members(ctx).items() if m["member_id"] == row["member_id"])
        kind = WORKER_KINDS[row["kind"]]
        return Question(id=question_id, owner=owner, version=row["version"], kind=kind,
            action_ref=(row["action"] or {}).get("action_ref", ""), snapshot_ref=claim.snapshot_ref,
            answer=self._answer(kind, row["answer"]))

    def has_permission(self, claim, owner, kind, action_ref):
        ctx = self._context(claim)
        member = self._members(ctx).get(owner)
        return member is not None and any(
            g["member_id"] == member["member_id"] and g["kind"] == KINDS[kind] and g["action_ref"] == action_ref and g["granted"]
            for g in ctx["grants"])

    def answer_question(self, owner, question_id, version, answer, request_key):
        """Answers as the owner (fixtures/demos). Requires their token."""
        if not isinstance(answer, bool):
            raise ContractError("INVALID_ANSWER")
        token = self.owner_tokens.get(owner)
        row = next((q for q in self.http.view(token, "my_questions") if q["request_key"] == question_id), None) if token else None
        if row is None:
            raise ContractError("NOT_AUTHORIZED")
        kind = WORKER_KINDS[row["kind"]]
        if row["state"] == "ANSWERED" and self._answer(kind, json.loads(row["answer_json"])) == answer:
            return question_id
        if kind in {"DISCLOSURE", "CONSULTATION", "EXPLORE"}:
            payload = {"granted": answer}
        elif kind == "PARTICIPATION" and answer:
            prefs = self.http.view(token, "my_participation_preferences")[0]
            payload = {k: prefs[k] for k in ("speak_up_for_me", "ask_me_first", "give_me_time", "reminders_opted_in")}
        elif kind == "PARTICIPATION":
            payload = {"confirmed": False}
        else:
            payload = {"answer": answer}
        self.http.call(token, "answer_question", question_id=row["id"], version=version, answer_json=json.dumps(payload))
        return question_id

    def park_job(self, claim, question_refs):
        ctx = self._context(claim)
        ids = {q["request_key"]: q["question_id"] for q in ctx["questions"]}
        if not question_refs or any(ref not in ids for ref in question_refs):
            raise ContractError("QUESTION_SUPERSEDED")
        self.http.call(self.token, "park_job", request_key=f"{claim.job_id}:park:{digest(list(question_refs))}",
            graph_thread_ref=claim.graph_thread_ref, question_ids=[ids[ref] for ref in question_refs], **self._attempt(claim))

    # ---------- dialogue ----------

    def submit_agent_message(self, claim, message, participants):
        message = Message.model_validate(primitive(message))
        ctx = self._context(claim)
        job = ctx["job"]
        names = {"mediator", *self._members(ctx)}
        if (not participants or len(set(participants)) != len(participants) or not set(participants) <= names
                or message.sender_agent_id not in participants or message.recipient_agent_id not in participants
                or message.sender_agent_id == message.recipient_agent_id):
            raise ContractError("NOT_AUTHORIZED")
        if message.room_id != str(job["room_id"]) or (message.input_revision, message.membership_revision,
                message.permission_revision) != (job["input_revision"], job["membership_revision"], job["permission_revision"]):
            raise ContractError("STALE_SNAPSHOT")
        if message.worker_validation.snapshot_ref != claim.snapshot_ref or \
                message.worker_validation.permission_revision != job["permission_revision"]:
            raise ContractError("INVALID_VALIDATION_METADATA")

        sent = {m["message_key"]: m for m in ctx["messages"]}
        content = message.checked_content()
        if message.message_id in sent:
            stored = sent[message.message_id]
            if (stored["type"], stored["utterance"], stored["payload"]) != (content["type"], content["utterance"], content["payload"]):
                raise ContractError("KEY_BODY_MISMATCH")
            return message.message_id

        sender, recipient = self._agent(ctx, message.sender_agent_id), self._agent(ctx, message.recipient_agent_id)
        agents = sorted(self._agent(ctx, p) for p in participants)
        round_id = f"round-{message.round_id}"
        thread = next((t for t in ctx["threads"] if t["topic_ref"] == message.thread_id), None)
        if thread is not None and (sorted(thread["participant_agent_ids"]) != agents or thread["round_id"] != round_id):
            raise ContractError("NOT_AUTHORIZED")
        reply_to = None
        if message.reply_to:
            if message.reply_to not in sent:
                raise ContractError("INVALID_REPLY")
            reply_to = sent[message.reply_to]["message_id"]
        audience_member = None
        if message.audience == "OWNER_PRIVATE" and message.sender_agent_id != "mediator":
            audience_member = self._members(ctx)[message.sender_agent_id]["member_id"]
        self.http.call(self.token, "submit_agent_message", message_key=message.message_id, schema_version=2,
            round_id=round_id, thread_id=some(thread["thread_id"]) if thread else None,
            open_thread=None if thread else some({"topic_ref": message.thread_id, "participant_agent_ids": agents,
                "turn_budget": some(self.limits.thread_turns), "successor_of": None}),
            sender_agent_id=sender, recipient_agent_id=recipient, type=message.type.value, reply_to=some(reply_to),
            draft_ref=some(message.payload.draft_ref), proposal_id=None, proposal_version=None,
            payload_json=json.dumps(content["payload"], sort_keys=True), utterance=message.utterance,
            claim_refs=list(message.claim_refs), evidence_refs=list(message.evidence_refs),
            audience=AUDIENCES[message.audience], audience_member_id=some(audience_member),
            sanitization_status=message.worker_validation.status,
            validation_digest=some(message.worker_validation.content_digest), **self._attempt(claim))
        return message.message_id

    def record_message_processed(self, claim, message_id, recipient, replies):
        ctx = self._context(claim)
        ids = {m["message_key"]: m["message_id"] for m in ctx["messages"]}
        if message_id not in ids or any(ref not in ids for ref in replies):
            raise ContractError("NOT_AUTHORIZED")
        self.http.call(self.token, "record_message_processed", message_id=ids[message_id],
            recipient_agent_id=self._agent(ctx, recipient), reply_ids=[ids[r] for r in replies], **self._attempt(claim))

    def close_threads(self, claim, reason):
        self.http.call(self.token, "close_threads", reason=reason, **self._attempt(claim))

    # ---------- results ----------

    def publish_proposal(self, claim, selected, candidates, report, key, explanation=None):
        report = ValidationReport.model_validate(primitive(report))
        if not report.valid or report.snapshot_ref != claim.snapshot_ref or report.selected != tuple(selected) \
                or report.candidate_digest != digest([primitive(c) for c in candidates]):
            raise ContractError("INVALID_VALIDATION_REPORT")
        ctx = self._context(claim)
        members = self._members(ctx)
        chosen = {c.id: c for c in (Candidate.model_validate(primitive(c)) for c in candidates)}
        records = [chosen[ref] for ref in selected]
        scope = "TRIP" if any(r.category != "FLIGHT" for r in records) else "FLIGHTS_ONLY"
        terms = {"scope": scope, "policy": POLICY, "selected": [primitive(r) for r in records]}
        summary = {"scope": scope, "travellers": sum(1 for r in records if r.category == "FLIGHT")}
        notes = {}
        if explanation is not None:
            summary.update(text=explanation.summary, group=explanation.group.model_dump(mode="json"),
                           suggestions=[i.model_dump(mode="json") for i in explanation.suggestions])
            notes = {n.impact.owner: {**n.impact.model_dump(mode="json"), "text": n.text} for n in explanation.impacts}
        result = self.http.procedure(self.token, "publish_proposal", request_key=f"{claim.job_id}:{key}", stage="TRAVEL",
            terms_json=json.dumps(terms, sort_keys=True, separators=(",", ":")),
            shared_summary_json=json.dumps(summary, sort_keys=True),
            validation_json=json.dumps(report.model_dump(mode="json"), sort_keys=True),
            evidence_refs=list(selected), affected_member_ids=[],
            impacts=[{"member_id": members[r.owner]["member_id"],
                      "impact_json": json.dumps(notes.get(r.owner, {"total_cents": r.total_cents}), sort_keys=True)}
                     for r in records if r.owner],
            selected_candidate_refs=list(selected), **self._attempt(claim))
        return str(result["proposal_id"])

    def notify_owner(self, claim, owner, text, key):
        member = self._members(self._context(claim)).get(owner)
        if member is None:
            raise ContractError("NOT_AUTHORIZED")
        self.http.call(self.token, "reply_to_owner", request_key=f"job{claim.generation}:note:{key}",
            member_id=member["member_id"], body=text, **self._attempt(claim))

    def room_event(self, claim, kind, payload, key):
        self.http.call(self.token, "emit_event", request_key=f"job{claim.generation}:event:{key}",
            type=kind, member_id=None, payload_json=json.dumps(payload, sort_keys=True), **self._attempt(claim))

    # ---------- onboarding ----------

    def load_intake(self, claim):
        ctx = self._context(claim)
        subject = ctx.get("subject")
        if subject is None:
            raise ContractError("NOT_HANDLED")
        by_id = {m["member_id"]: m for m in ctx["members"]}
        me = by_id[subject["member_id"]]
        trip = ctx.get("trip_brief") or {}
        intake = subject.get("intake") or {}
        return IntakeContext(member_id=me["member_id"], owner=self.owner_name(me), display_name=me["display_name"],
            purpose=subject["purpose"], is_organizer=me["role"] == "ORGANIZER",
            others=tuple(m["display_name"] for m in ctx["members"] if m["active"] and m["member_id"] != me["member_id"]),
            description=trip.get("description", ""),
            brief=TripBrief.model_validate(trip["brief"]) if trip.get("state") == "CONFIRMED" else None,
            brief_state=trip.get("state"),
            messages=tuple(IntakeMessage(id=m["id"], sender=m["sender"], body=m["body"]) for m in subject["messages"]),
            pending=tuple(p["patch"] for p in subject["patches"] if p["state"] == "PENDING"),
            confirmed_keys=tuple(c["key"] for c in ctx["constraints"] if c["member_id"] == me["member_id"]),
            intake_state=intake.get("state"), coverage=intake.get("coverage") or {},
            question_count=intake.get("question_count", 0),
            preferences_confirmed=me["participation_preferences"]["confirmed"])

    def _member_id(self, claim, owner):
        member = self._members(self._context(claim)).get(owner)
        if member is None:
            raise ContractError("NOT_AUTHORIZED")
        return member["member_id"]

    def propose_patch(self, claim, owner, patch, source_message_id, key):
        self.http.call(self.token, "propose_constraint_patch", request_key=f"job{claim.generation}:patch:{key}",
            member_id=self._member_id(claim, owner), patch_json=json.dumps(patch, sort_keys=True),
            source_message_id=some(source_message_id) if source_message_id is not None else None, **self._attempt(claim))

    def withdraw_patches(self, claim, owner, key):
        self.http.call(self.token, "withdraw_constraint_patches", request_key=f"job{claim.generation}:withdraw:{key}",
            member_id=self._member_id(claim, owner), **self._attempt(claim))

    def update_intake(self, claim, owner, coverage, state, asked, key):
        self.http.call(self.token, "update_intake", request_key=f"job{claim.generation}:intake:{key}",
            member_id=self._member_id(claim, owner), coverage_json=json.dumps(coverage, sort_keys=True), state=state,
            asked_question=asked, **self._attempt(claim))

    def propose_preferences(self, claim, owner, speak_up, ask_first, give_time, key):
        self.http.call(self.token, "propose_participation_preferences", request_key=f"job{claim.generation}:prefs:{key}",
            member_id=self._member_id(claim, owner), speak_up_for_me=speak_up, ask_me_first=ask_first,
            give_me_time=give_time, **self._attempt(claim))

    def propose_trip_brief(self, claim, brief, unclear, key):
        self.http.call(self.token, "propose_trip_brief", request_key=f"job{claim.generation}:brief:{key}",
            brief_json=json.dumps(TripBrief.model_validate(primitive(brief)).model_dump(mode="json"), sort_keys=True),
            unclear_json=json.dumps(list(unclear)), **self._attempt(claim))

    def finish_job(self, claim, outcome):
        self.http.call(self.token, "finish_job", request_key=f"{claim.job_id}:{claim.attempt_token}:finish:{outcome}",
            outcome=outcome, outcome_json="{}", **self._attempt(claim))

    def fail_job(self, claim, code):
        self.http.call(self.token, "fail_job", request_key=f"{claim.job_id}:{claim.attempt_token}:fail:{code}",
            retryable=False, code=code, detail="", **self._attempt(claim))


def seed_room(http: StdbHttp, briefs, goal: str = "Group trip", settings: dict | None = None) -> dict:
    """Creates one human identity per brief (display name = owner), confirms the
    settings each brief marks as confirmed, and enters budgets as HARD
    constraints with the brief's private reason. For demos and tests without the
    frontend; returns room_id, invite_code and each owner's token (keep secret)."""
    tokens = {b.owner: http.mint_identity()["token"] for b in briefs}
    organizer = briefs[0].owner
    http.call(tokens[organizer], "create_room", goal=goal, display_name=organizer)
    room = max(http.view(tokens[organizer], "my_rooms"), key=lambda r: r["id"])
    for b in briefs[1:]:
        http.call(tokens[b.owner], "join_room", invite_code=room["invite_code"], display_name=b.owner)
    for b in briefs:
        token = tokens[b.owner]
        if b.preferences.confirmed:
            http.call(token, "confirm_participation_preferences", speak_up_for_me=b.preferences.speak_up_for_me,
                      ask_me_first=b.preferences.ask_me_first, give_me_time=b.preferences.give_me_time,
                      reminders_opted_in=b.reminders_opted_in)
        http.call(token, "set_constraint", room_id=room["id"], key="flight_budget_total", value_json=str(b.hard_cap_cents),
                  unit="USD_cents", hardness="HARD", authorized_range_json=None,
                  private_rationale=some(b.private_reason) if b.private_reason else None)
        extra = {"baggage_required": (b.baggage_required or None, ""), "trip_budget_total": (b.trip_cap_cents, "USD_cents"),
                 "own_bedroom": (b.own_bedroom or None, ""), "stay_min_rating": (b.stay_min_rating, "")}
        for key, (value, unit) in extra.items():
            if value is not None:
                http.call(token, "set_constraint", room_id=room["id"], key=key, value_json=json.dumps(value), unit=unit,
                          hardness="HARD", authorized_range_json=None, private_rationale=None)
    if settings:
        http.call(tokens[organizer], "set_room_settings", room_id=room["id"], settings_json=json.dumps(settings))
    return {"room_id": room["id"], "invite_code": room["invite_code"], "owner_tokens": tokens}
