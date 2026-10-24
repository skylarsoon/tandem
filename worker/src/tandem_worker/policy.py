"""All natural-language checks run in the worker, never the backend."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def local_time(epoch: int, zone: str | None) -> str:
    try:
        return datetime.fromtimestamp(epoch, ZoneInfo(zone)).strftime("%H:%M") + " local time"
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M") + " UTC"


def candidate_facts(candidate: dict) -> dict[str, str]:
    """Room-safe facts about one option. Each cites `<candidate id>#<field>` so the
    backend can resolve the claim to recorded evidence. Never caps or reasons."""
    ref = candidate["id"]
    complete = candidate.get("cost_basis") == "COMPLETE_FLIGHT_TOTAL" and not candidate.get("missing_critical_fields")
    facts = {}
    if candidate.get("total_cents") is not None:
        facts[f"{ref}#price"] = (f"The fare is {money(candidate['total_cents'])} including taxes." if complete else
                                 f"The listed fare is {money(candidate['total_cents'])}; some fees are not confirmed.")
    facts[f"{ref}#arrival"] = f"It lands at {local_time(candidate['arrival_epoch'], candidate.get('arrival_timezone'))}."
    bag = candidate.get("baggage_included")
    facts[f"{ref}#bag"] = ("A checked bag is included." if bag is True else
                           "No checked bag is included." if bag is False else "Checked-bag coverage is not confirmed.")
    return facts


def permitted_facts(act: Act, payload: Payload, candidate: dict | None = None,
                    group_window: tuple[int, int, str | None] | None = None) -> dict[str, str]:
    # Fixed sentences deliberately exclude raw reasons, owner caps, burden and
    # headroom. Candidate facts come only from typed evidence (candidate_facts);
    # personal trade-offs are never phrased for the room.
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
    # An objection says which kind of requirement fails, never its value. It only
    # reaches the room with speak-up-for-me or a disclosure grant (backend P03).
    by_blocker = {
        (Act.OBJECT, "HARD_LIMIT"): "This option is outside what my traveller can commit to.",
        (Act.OBJECT, "BAGGAGE_UNKNOWN"): "My traveller needs a checked bag, and this fare doesn't confirm one.",
        (Act.CLARIFY, "BAGGAGE_UNKNOWN"): "Could you confirm whether this fare includes a checked bag?",
        (Act.COUNTERPROPOSE, "BAGGAGE_UNKNOWN"): "Let's find a fare that confirms a checked bag before choosing this one.",
        (Act.COUNTERPROPOSE, "HARD_LIMIT"): "Let's look for a cheaper alternative before choosing this one.",
    }
    sentence = by_blocker.get((act, payload.blocker), sentences[act])
    if act == Act.EVALUATE and payload.evaluation == "ELIGIBLE":
        sentence = "This works for my traveller; they will still review and approve it themselves."
    facts = {f"act:{act.value}": sentence}
    if candidate is not None:
        facts.update(candidate_facts(candidate))
    if group_window is not None:
        earliest, latest, zone = group_window
        facts[f"draft:{payload.draft_ref}#arrival_window"] = (
            f"Everyone would land between {local_time(earliest, zone).replace(' local time', '')} and {local_time(latest, zone)}.")
    return facts


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
