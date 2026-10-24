"""All natural-language checks run in the worker, never the backend."""
from __future__ import annotations

from .contracts import Act, Brief, Decision, Payload, VoiceDraft, ContractError


def needed_permissions(brief: Brief, decision: Decision) -> list[str]:
    if not brief.preferences.confirmed:
        return ["PARTICIPATION"]
    result = []
    if decision.act == Act.OBJECT and not brief.preferences.speak_up_for_me:
        result.append("DISCLOSURE")
    if decision.concession and brief.preferences.ask_me_first:
        result.append("CONSULTATION")
    if decision.exceeds_exploration:
        result.append("EXPLORE")
    return result


def review_controls(brief: Brief) -> dict:
    return {"reminder_allowed": brief.preferences.confirmed and brief.reminders_opted_in and not brief.preferences.give_me_time,
            "allow_decide_later": True, "urgency_allowed": False}


def permitted_facts(act: Act, payload: Payload) -> dict[str, str]:
    # Fixed sentences deliberately exclude raw reasons, owner caps, burden and
    # headroom. Candidate-specific facts can be added only through typed evidence.
    sentences = {
        Act.PROPOSE: "Could you evaluate this option?",
        Act.EVALUATE: "My evaluation is complete; human review is still required.",
        Act.OBJECT: "One participant's requirements are not met by this option.",
        Act.CLARIFY: "Could you check an alternative with the required evidence?",
        Act.COUNTERPROPOSE: "Please research an alternative before selecting this option.",
        Act.OWNER_RESPONSE: "An authenticated owner response is available.",
        Act.VALIDATE_REQUEST: "Please validate the selected option.",
        Act.VALIDATION_RESULT: "The modeled checks have been completed.",
        Act.REQUEST_HUMAN_REVIEW: "Please review the exact proposal terms.",
        Act.ROUND_PAUSED: "Planning is paused.",
        Act.ROUND_CLOSED: "This discussion is closed.",
    }
    return {f"act:{act.value}": sentences[act]}


def validate_voice(draft: VoiceDraft, facts: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    """Exact grounded segments avoid pretending regex/LLMs can prove semantics.

    The tradeoff is constrained expression in v1. Arbitrary free prose would
    require an explicitly evaluated worker-side semantic reviewer.
    """
    seen = set()
    for segment in draft.segments:
        if segment.fact_ref in seen or facts.get(segment.fact_ref) != segment.text:
            raise ContractError("UTTERANCE_PAYLOAD_MISMATCH")
        seen.add(segment.fact_ref)
    return draft.connector.join(s.text for s in draft.segments), tuple(s.fact_ref for s in draft.segments)


def fallback_voice(facts: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    return " ".join(facts.values()), tuple(facts)
