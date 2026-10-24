"""Plain-language explanations of solver results.

Room text never names anyone or mentions caps, reasons or one person's
trade-off. Owner notes may use that owner's own numbers, and go only to them.
"""
from __future__ import annotations

from collections import Counter

from .contracts import Brief, Conflict, Explanation, GroupSummary, Impact, ImpactNote
from .policy import local_time, money


def duration(seconds: int) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours}h" if not minutes else f"{hours}h{minutes:02d}"


def group_text(group: GroupSummary, zone: str | None) -> str:
    text = (f"Total {money(group.total_cents)}. Everyone lands between {local_time(group.earliest_arrival, zone).replace(' local time', '')} "
            f"and {local_time(group.latest_arrival, zone)}, within the group's {duration(group.arrival_window_seconds)} window.")
    if group.cheapest_total_cents is not None:
        text += (f" Landing together costs {money(group.total_cents - group.cheapest_total_cents)} more than everyone's cheapest fares"
                 + (f", which would land {duration(group.cheapest_spread_seconds)} apart." if group.cheapest_spread_seconds else "."))
    if group.travellers_paying_extra:
        who = "one traveller" if group.travellers_paying_extra == 1 else f"{group.travellers_paying_extra} travellers"
        text += f" The extra cost falls on {who}, kept as small as possible for whoever pays most."
    return text


def impact_text(impact: Impact, window_seconds: int) -> str:
    price = money(impact.price_cents)
    if impact.extra_cents == 0:
        return f"Your flight costs {price}, your cheapest valid fare."
    why = (f"so everyone lands within {duration(window_seconds)} of each other"
           if impact.reason == "ARRIVAL_WINDOW" else "to keep the group's trade-offs balanced")
    return (f"Your flight costs {price}: {money(impact.extra_cents)} more than your cheapest valid fare "
            f"({money(impact.cheapest_alone_cents)}), {why}.")


def consultation_prompt(impact: Impact, window_seconds: int) -> str:
    return impact_text(impact, window_seconds) + " May your agent accept this on your behalf?"


def explanation(group: GroupSummary, impacts: tuple[Impact, ...], zone: str | None) -> Explanation:
    return Explanation(summary=group_text(group, zone), group=group,
        impacts=tuple(ImpactNote(impact=i, text=impact_text(i, group.arrival_window_seconds)) for i in impacts))


def owner_conflict_note(conflict: Conflict, brief: Brief) -> str:
    if conflict.code == "HARD_LIMIT":
        return (f"No plan fits yet: the cheapest complete fare found for you is {money(conflict.cheapest_cents)}, "
                f"above your {money(brief.hard_cap_cents)} flight limit. Raising your limit to at least "
                f"{money(conflict.cheapest_cents)} would unblock the plan.")
    if conflict.code == "BAGGAGE_UNAVAILABLE":
        return ("No plan fits yet: none of the fares found for you confirm a checked bag. "
                "Dropping the bag requirement, or another search, would unblock the plan.")
    return "No plan fits yet: no fare with a complete price was found for your route."


def room_conflict_event(conflicts: tuple[Conflict, ...]) -> dict:
    """Neutral counts by cause; never which traveller is blocked."""
    payload: dict = {"blocked_travellers": dict(sorted(Counter(c.code for c in conflicts if c.owner).items()))}
    arrival = next((c for c in conflicts if c.code == "ARRIVAL_WINDOW"), None)
    lines = []
    for code, count in payload["blocked_travellers"].items():
        who = "1 traveller" if count == 1 else f"{count} travellers"
        lines.append({"HARD_LIMIT": f"{who}: every fare found is above a private limit.",
                      "BAGGAGE_UNAVAILABLE": f"{who}: no fare found confirms a required checked bag.",
                      "NO_CANDIDATES": f"{who}: no fare with a complete price was found."}[code])
    if arrival:
        payload.update(closest_spread_seconds=arrival.closest_spread_seconds, required_seconds=arrival.required_seconds)
        lines.append(f"No combination of flights lands everyone within {duration(arrival.required_seconds)}; "
                     f"the closest is {duration(arrival.closest_spread_seconds)} apart.")
    if payload["blocked_travellers"]:
        lines.append("Each affected traveller has a private note from their agent.")
    payload["text"] = "No plan fits yet. " + " ".join(lines)
    return payload
