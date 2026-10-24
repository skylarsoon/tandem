"""All natural-language checks run in the worker, never the backend."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import COMPLETE_BASIS, Act, Brief, Decision, Payload, VoiceDraft, ContractError


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
    category = candidate.get("category", "FLIGHT")
    complete = candidate.get("cost_basis") == COMPLETE_BASIS.get(category) and not candidate.get("missing_critical_fields")
    if category != "FLIGHT":
        return shared_facts(candidate, complete)
    facts = {}
    if candidate.get("total_cents") is not None:
        facts[f"{ref}#price"] = (f"The fare is {money(candidate['total_cents'])} including taxes." if complete else
                                 f"The listed fare is {money(candidate['total_cents'])}; some fees are not confirmed.")
    facts[f"{ref}#arrival"] = f"It lands at {local_time(candidate['arrival_epoch'], candidate.get('arrival_timezone'))}."
    bag = candidate.get("baggage_included")
    facts[f"{ref}#bag"] = ("A checked bag is included." if bag is True else
                           "No checked bag is included." if bag is False else "Checked-bag coverage is not confirmed.")
    return facts


def shared_facts(candidate: dict, complete: bool) -> dict[str, str]:
    ref, price = candidate["id"], candidate.get("total_cents")
    facts = {}
    if candidate.get("title"):
        facts[f"{ref}#title"] = f"The {'stay' if candidate['category'] == 'STAY' else 'transfer'} is {candidate['title']}."
    if candidate["category"] == "STAY":
        if price is not None:
            facts[f"{ref}#price"] = (f"The stay costs {money(price)} in total, taxes included." if complete else
                                     f"The stay is listed at {money(price)}; some fees are not confirmed.")
        if candidate.get("capacity") is not None:
            rooms = candidate.get("bedrooms")
            facts[f"{ref}#rooms"] = (f"It sleeps {candidate['capacity']}" +
                                     ("." if rooms is None else " in a studio." if rooms == 0 else
                                      f" with {rooms} bedroom{'s' if rooms != 1 else ''}."))
        if candidate.get("rating") is not None:
            facts[f"{ref}#rating"] = f"Guests rate it {candidate['rating']:.2g} out of 5."
    elif candidate["category"] == "TRANSFER":
        if price is not None:
            facts[f"{ref}#price"] = (f"The airport transfer costs {money(price)} for the whole vehicle." if complete else
                                     f"The airport transfer is listed at {money(price)}; some fees are not confirmed.")
        if candidate.get("capacity") is not None:
            facts[f"{ref}#seats"] = f"It seats {candidate['capacity']}."
    return facts


def permitted_facts(act: Act, payload: Payload, candidate: dict | list[dict] | None = None,
                    group_window: tuple[int, int, str | None] | None = None, share_cents: int = 0) -> dict[str, str]:
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
    for one in [candidate] if isinstance(candidate, dict) else candidate or []:
        facts.update(candidate_facts(one))
    if share_cents:
        facts[f"draft:{payload.draft_ref}#share"] = f"Shared costs come to {money(share_cents)} per traveller."
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
    return join_facts([s.text for s in draft.segments], draft.connector), tuple(s.fact_ref for s in draft.segments)


def join_facts(texts: list[str], connector: str = " ") -> str:
    """"Also, the fare…" rather than "Also, The fare…"; the cited text is unchanged otherwise."""
    if connector == " ":
        return " ".join(texts)
    return texts[0] + "".join(connector + t[:1].lower() + t[1:] for t in texts[1:])


def fallback_voice(facts: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    return " ".join(facts.values()), tuple(facts)
