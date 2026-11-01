"""Public wording follows the negotiated act; it never drives or replaces it.

The deterministic checks reject unknown references/numbers. An independent model
review checks semantics; this is defense in depth, not proof of factuality.
Only room-safe facts enter either call. Failed reviews publish a labelled fallback.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pydantic import ValidationError

from .contracts import ConversationDraft, ConversationReview, ContractError, digest
from .policy import fallback_voice


def numbers(text):
    return {Decimal(n.replace(",", "")) for n in re.findall(r"(?<![\w])\d+(?:,\d{3})*(?:\.\d+)?", text)}


def check_draft(draft, facts, act):
    refs = draft.claim_refs
    if (f"act:{act}" not in refs or len(set(refs)) != len(refs)
            or any(ref not in facts for ref in refs) or not draft.text.strip()
            or len(draft.text.split()) > 140):
        raise ContractError("UTTERANCE_PAYLOAD_MISMATCH")
    supported = " ".join(facts[ref] for ref in refs)
    if not numbers(draft.text) <= numbers(supported):
        raise ContractError("UTTERANCE_PAYLOAD_MISMATCH")


def grounded_turn(backend, claim, model, *, act, facts, operation_ref, role,
                  evaluation, blocker, recent_turns=(), audience="ROOM"):
    # Shared turns receive room-safe facts only; OWNER_PRIVATE may receive
    # the authenticated recipient's own confirmed details.
    context = {"act": act.value, "evaluation": evaluation, "blocker": blocker,
               "role": role, "facts": facts, "recent_turns": list(recent_turns[-6:])}
    if audience == "OWNER_PRIVATE":
        context["audience"] = audience
    for attempt in range(2):
        try:
            key = f"conversation:{operation_ref}:{digest(context)}:{attempt}"
            raw = backend.operation(claim, key, context, "model", lambda: model.converse(context))
            draft = ConversationDraft.model_validate(raw)
            check_draft(draft, facts, act.value)
            review_context = {"act": act.value, "evaluation": evaluation, "blocker": blocker,
                              "facts": {ref: facts[ref] for ref in draft.claim_refs},
                              "draft": draft.model_dump(mode="json")}
            if audience == "OWNER_PRIVATE":
                review_context["audience"] = audience
            review_raw = backend.operation(claim, key + ":review:" + digest(review_context), review_context,
                                           "model", lambda: model.review_conversation(review_context))
            review = ConversationReview.model_validate(review_raw)
            if not all(review.model_dump().values()):
                raise ContractError("UTTERANCE_PAYLOAD_MISMATCH")
            backend.check(claim)
            return draft.text, draft.claim_refs, "VALIDATED"
        except (ValidationError, ValueError, ContractError) as exc:
            # Lease, permission, provider and cost-limit failures must remain visible.
            if isinstance(exc, ContractError) and exc.code != "UTTERANCE_PAYLOAD_MISMATCH":
                raise
    # A failed generation must not dump an entire evidence table into chat.
    # Keep the canonical intent plus a bounded, relevant status fallback.
    fallback = facts
    if audience == "OWNER_PRIVATE":
        chosen = [k for k in facts if k.startswith('act:') or k in {'workflow','status','planning_job'}]
        fallback = {k:facts[k] for k in chosen[:3]}
    text, refs = fallback_voice(fallback)
    return text, refs, "TEMPLATE_FALLBACK"
