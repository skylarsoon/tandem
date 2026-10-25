"""Explanations built from rules: what blocked a plan, in the person's own words.
Private notes only; the room hears neutral counts (explain.room_conflict_event)."""
from __future__ import annotations

from .contracts import Conflict
from .intake_policy import describe_rule
from .rules import RoomRules


def rule_conflict_note(conflict: Conflict, rules: RoomRules) -> str:
    """No option found for this person meets their own rules and the trip's dates together."""
    own, trip = [], []
    for attribute in conflict.attributes:
        for rule in rules.binding(conflict.owner):
            if rule.attribute != attribute:
                continue
            if rule.owner is None:  # the trip's own dates
                trip.append(f"the trip's dates ({rule.source_quote})")
            elif rule.source_quote:
                own.append(f"“{rule.source_quote}” ({_lower_first(describe_rule(rule))})")
            else:
                own.append(_lower_first(describe_rule(rule)))
    own, trip = list(dict.fromkeys(own)), list(dict.fromkeys(trip))
    if not own and not trip:
        return "No plan fits yet: none of the options found for you meets your limits."
    said = ("what you said, " + _join(own)) if own else ""
    joined = _join([x for x in (said, *trip) if x])
    return (f"No plan fits yet: none of the options found for you fits {joined} together. "
            "Changing one of them, or another search, would unblock it.")


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def declined_note(conflict: Conflict) -> str:
    """They said no to each option they were asked about; that's their call, said back plainly."""
    n = conflict.declined
    found = "the option" if n == 1 else f"each of the {n} options"
    return (f"No plan fits yet: you said no to {found} I asked you about. Tell me what you'd accept "
            "(a connection, a higher fare, another time), or I can look for more options.")
