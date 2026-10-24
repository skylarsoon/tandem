from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ..contracts import Act, ContractError, Decision, Impact, Message, Payload, Question, ValidationMetadata, VoiceDraft, digest
from ..explain import consultation_prompt
from ..solver import RELAXABLE, relaxed
from ..policy import fallback_voice, needed_permissions, permitted_facts, validate_voice


UNRESOLVED_ACTS = {Act.OBJECT, Act.CLARIFY, Act.COUNTERPROPOSE}
ACTIONABLE_BLOCKERS = {"HARD_LIMIT", "BAGGAGE_UNKNOWN"}
UNSURE_QUESTION = "Your agent has a concern about this plan that another search can't fix. Does this plan work for you?"


class PersonalState(TypedDict, total=False):
    task: dict
    decision: dict
    questions: list[str]
    message_ref: str
    outgoing_payload: dict
    outgoing_thread_id: str


def question_for(claim, owner, kind, action_ref, prompt=""):
    return Question(id="question-" + digest([claim.snapshot_ref, owner, kind, action_ref])[:24],
        owner=owner, kind=kind, action_ref=action_ref, snapshot_ref=claim.snapshot_ref, prompt=prompt)


def owner_prompt(kind, payload, act, impact, window_seconds):
    """Owner-only wording of exactly what the agent wants to do."""
    if kind == "CONSULTATION" and impact is not None:
        return consultation_prompt(impact, window_seconds)
    if kind == "DISCLOSURE":
        said = permitted_facts(act, payload)[f"act:{act.value}"]
        return f'May your agent tell the group: "{said}"? It will not share your limit or your reasons.'
    return ""


def missing_answered(backend, claim, owner, action_ref):
    question = question_for(claim, owner, "MISSING_FACT", action_ref)
    try:
        return backend.get_question(claim, question.id).answer is True
    except KeyError:
        return False


def build_personal(backend, claim, model, impact_of=lambda owner: None):
    """Per-invocation graph: all private context is ephemeral in node locals.

    Only safe typed decisions/references return to the coordinator. Clients and
    attempt tokens are closure dependencies, never checkpoint state.
    """
    def decide(state):
        task = state["task"]
        backend.check(claim)
        original = backend.read_brief(claim, task["owner"])
        # Requirements the owner agreed to set aside for this trip no longer bind.
        snapshot_revision = task["snapshot"]["input_revision"]
        [brief] = relaxed([original], snapshot_revision, lambda owner, ref: backend.has_permission(claim, owner, "CONSULTATION", ref))
        context = {"owner": brief.owner, "hard_cap_cents": brief.hard_cap_cents,
            "private_reason": brief.private_reason, "preferences": brief.preferences.model_dump(mode="json"),
            "baggage_required": brief.baggage_required, "candidate": task["candidate"],
            "trip_cap_cents": brief.trip_cap_cents, "own_bedroom": brief.own_bedroom, "stay_min_rating": brief.stay_min_rating,
            "shared": task.get("shared", []), "share_cents": task.get("share_cents", 0),
            "owner_accepted_this_plan_cost": backend.has_permission(claim, original.owner, "CONSULTATION", task["draft_ref"]),
            "set_aside_by_owner": [field for field, _ in RELAXABLE.values() if getattr(brief, field) != getattr(original, field)],
            "sender": task["sender"], "inbound_type": task["inbound_type"],
            "inbound_payload": task["inbound_payload"], "inbound_evidence": task["inbound_evidence"],
            "permitted_recipients": task["permitted_recipients"], "impact": impact_of(task["owner"]),
            "missing_fact_answered": missing_answered(backend, claim, brief.owner, task["draft_ref"])}
        decision = None
        for attempt in range(2):
            try:
                raw = backend.operation(claim, f"decision:{digest(context)}:{attempt}", context, "model", lambda: model.decide(context))
                decision = Decision.model_validate(raw)
                if decision.act not in {Act.EVALUATE, Act.OBJECT, Act.COUNTERPROPOSE, Act.CLARIFY} or decision.recipient not in task["permitted_recipients"] or decision.recipient == brief.owner:
                    raise ValueError("INVALID_AGENT_ACT")
                break
            except (ValidationError, ValueError):
                decision = None
        if decision is None:
            raise ContractError("INVALID_MODEL_OUTPUT")
        # Model eligibility cannot override numeric checks or required evidence.
        candidate = task["candidate"]
        trip_cost = None if candidate["total_cents"] is None else candidate["total_cents"] + context["share_cents"]
        if (trip_cost is None or candidate["total_cents"] > brief.hard_cap_cents
                or (brief.trip_cap_cents is not None and trip_cost > brief.trip_cap_cents)):
            decision = Decision(act=Act.OBJECT, evaluation="INELIGIBLE", blocker="HARD_LIMIT")
        elif brief.baggage_required and candidate["baggage_included"] is not True and decision.evaluation == "ELIGIBLE":
            decision = Decision(act=Act.OBJECT, evaluation="INCOMPLETE_EVIDENCE", blocker="BAGGAGE_UNKNOWN", research=True)
        # A concern no search can act on (no over-limit, no missing bag) goes to the
        # owner, once, instead of burning research rounds; their yes settles it.
        if decision.act in UNRESOLVED_ACTS and decision.blocker not in ACTIONABLE_BLOCKERS:
            decision = Decision() if context["missing_fact_answered"] else Decision(
                evaluation="NEEDS_OWNER_INPUT", blocker="MISSING_FACT",
                owner_question=decision.owner_question.strip() or UNSURE_QUESTION)
        # Accepting a fare that costs more than the owner's cheapest valid one is a
        # concession, whatever the model called it; Ask me first then consults them.
        impact = Impact.model_validate(context["impact"]) if context["impact"] else None
        consulted = backend.has_permission(claim, brief.owner, "CONSULTATION", task["draft_ref"])
        if impact and impact.extra_cents > 0 and (decision.evaluation == "ELIGIBLE" or (
                # Asking "is the extra cost OK?" is exactly the consultation Ask me first
                # requires: route it there so the answer becomes a scoped, enforced grant.
                decision.evaluation == "NEEDS_OWNER_INPUT" and brief.preferences.ask_me_first and not consulted)):
            decision = Decision(act=Act.EVALUATE, evaluation="ELIGIBLE", recipient="mediator", concession=True)
        payload = Payload(draft_ref=task["draft_ref"], evaluation=decision.evaluation, blocker=decision.blocker,
            candidate_refs=(candidate["id"], *(c["id"] for c in task.get("shared", []))))
        questions = []
        for kind in needed_permissions(brief, decision):
            if kind != "PARTICIPATION" and backend.has_permission(claim, brief.owner, kind, task["draft_ref"]):
                continue
            q = question_for(claim, brief.owner, kind, task["draft_ref"],
                owner_prompt(kind, payload, decision.act, impact, task.get("window_seconds", 0)))
            questions.append(backend.ask_owner(claim, q))
        if decision.evaluation == "NEEDS_OWNER_INPUT":
            q = question_for(claim, brief.owner, "MISSING_FACT", task["draft_ref"], decision.owner_question.strip())
            questions.append(backend.ask_owner(claim, q))
        return {"decision": decision.model_dump(mode="json"), "questions": questions}

    def voice_and_send(state):
        if state["questions"]:
            return {}
        task = state["task"]
        decision = Decision.model_validate(state["decision"])
        payload = Payload(draft_ref=task["draft_ref"], evaluation=decision.evaluation, blocker=decision.blocker,
            candidate_refs=(task["candidate"]["id"], *(c["id"] for c in task.get("shared", []))), research=decision.research,
            concession=decision.concession, exceeds_exploration=decision.exceeds_exploration)
        # The mediator states the group window; each agent speaks to its own fare.
        facts = permitted_facts(decision.act, payload, task["candidate"], share_cents=task.get("share_cents", 0))
        context = {"act": decision.act.value, "facts": facts}
        text, refs = fallback_voice(facts)
        status = "TEMPLATE_FALLBACK"
        for attempt in range(2):
            try:
                raw = backend.operation(claim, f"voice:{task['operation_ref']}:{digest(context)}:{attempt}", context, "model", lambda: model.voice(context))
                text, refs = validate_voice(VoiceDraft.model_validate(raw), facts)
                status = "VALIDATED"
                break
            except (ValidationError, ValueError, ContractError) as exc:
                if isinstance(exc, ContractError) and exc.code != "UTTERANCE_PAYLOAD_MISMATCH":
                    raise
        # Check revocation/lease after model generation, before submission.
        backend.check(claim)
        same_thread = decision.recipient == task["sender"]
        thread_id = task["thread_id"] if same_thread else "conversation-" + digest([task["operation_ref"], decision.recipient])[:24]
        participants = tuple(sorted({"mediator", task["owner"], decision.recipient}))
        ref = send_message(backend, claim, task["snapshot"], task["owner"], decision.recipient,
            decision.act, payload, thread_id, participants, task["round"], text, refs, status,
            reply_to=task["inbound_ref"] if same_thread else None,
            operation_ref=task["operation_ref"])
        backend.record_message_processed(claim, task["inbound_ref"], task["owner"], (ref,))
        return {"message_ref": ref, "outgoing_payload": payload.model_dump(mode="json"), "outgoing_thread_id": thread_id}

    builder = StateGraph(PersonalState)
    builder.add_node("decide_and_enforce", decide)
    builder.add_node("phrase_check_and_send", voice_and_send)
    builder.add_edge(START, "decide_and_enforce")
    builder.add_edge("decide_and_enforce", "phrase_check_and_send")
    builder.add_edge("phrase_check_and_send", END)
    return builder.compile()


def send_message(backend, claim, snapshot, sender, recipient, act, payload, thread_id, participants, round_id,
                 text=None, refs=None, status="TEMPLATE_FALLBACK", reply_to=None, operation_ref="",
                 candidate=None, group_window=None, share_cents=0):
    if text is None:
        text, refs = fallback_voice(permitted_facts(act, payload, candidate, group_window, share_cents))
    evidence = payload.candidate_refs
    content = {"type": act.value, "utterance": text, "payload": payload.model_dump(mode="json"), "claim_refs": refs, "evidence_refs": evidence}
    key = digest([claim.generation, operation_ref, thread_id, sender, recipient, act, content, reply_to])
    metadata = ValidationMetadata(snapshot_ref=claim.snapshot_ref, permission_revision=snapshot["permission_revision"],
        status=status, content_digest=digest(content))
    message = Message(message_id="msg-" + key[:24], room_id=snapshot["room_id"], thread_id=thread_id, round_id=round_id,
        input_revision=snapshot["input_revision"], membership_revision=snapshot["membership_revision"],
        permission_revision=snapshot["permission_revision"], sender_agent_id=sender, recipient_agent_id=recipient,
        type=act, reply_to=reply_to, payload=payload, utterance=text, evidence_refs=evidence, claim_refs=refs,
        idempotency_key=key, worker_validation=metadata)
    return backend.submit_agent_message(claim, message, participants)
