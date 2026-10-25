"""Plain-language explanations of solver results.

Room text never names anyone or mentions caps, reasons or one person's
trade-off. Owner notes may use that owner's own numbers, and go only to them.
"""
from __future__ import annotations

from collections import Counter

from .contracts import Brief, Candidate, Conflict, Explanation, GroupSummary, Impact, ImpactNote, Suggestion
from .policy import local_time, money


def duration(seconds: int) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours}h" if not minutes else f"{hours}h{minutes:02d}"


def group_text(group: GroupSummary, zone: str | None, titles: dict[str, str] | None = None) -> str:
    titles = titles or {}
    text = (f"Total {money(group.total_cents)}. Everyone lands between {local_time(group.earliest_arrival, zone).replace(' local time', '')} "
            f"and {local_time(group.latest_arrival, zone)}, within the group's {duration(group.arrival_window_seconds)} window.")
    if group.cheapest_total_cents is not None:
        text += (f" Landing together costs {money(group.total_cents - group.cheapest_total_cents)} more than everyone's cheapest fares"
                 + (f", which would land {duration(group.cheapest_spread_seconds)} apart." if group.cheapest_spread_seconds else "."))
    if group.travellers_paying_extra:
        who = "one traveller" if group.travellers_paying_extra == 1 else f"{group.travellers_paying_extra} travellers"
        text += f" The extra cost falls on {who}, kept as small as possible for whoever pays most."
    if group.stay:
        text += f" Stay: {titles.get(group.stay, 'one shared place')}, {money(group.stay_total_cents)} in total."
        if group.stay_premium_cents:
            text += (f" The group's room needs add {money(group.stay_premium_cents)} over the cheapest place "
                     "that sleeps everyone.")
    if group.transfer:
        text += f" Airport transfer: {titles.get(group.transfer, 'one shared vehicle')}, {money(group.transfer_total_cents)}."
    if group.share_each_cents:
        text += f" Shared costs are split equally: {money(group.share_each_cents)} each."
    return text


def impact_text(impact: Impact, window_seconds: int) -> str:
    why = {"ARRIVAL_WINDOW": f"so everyone lands within {duration(window_seconds)} of each other",
           "SHARED_CHOICE": "because of the stay and transfer the group's limits allow"}.get(
               impact.reason, "to keep the group's trade-offs balanced")
    if impact.share_cents:
        text = (f"Your trip costs {money(impact.price_cents)}: your {money(impact.flight_cents)} flight plus a "
                f"{money(impact.share_cents)} equal share of the stay and transfer.")
        if impact.extra_cents == 0:
            return text + " That's the cheapest trip available to you."
        return text + (f" That's {money(impact.extra_cents)} more than the cheapest trip available to you "
                       f"({money(impact.cheapest_alone_cents)}), {why}.")
    price = money(impact.price_cents)
    if impact.extra_cents == 0:
        return f"Your flight costs {price}, your cheapest valid fare."
    return (f"Your flight costs {price}: {money(impact.extra_cents)} more than your cheapest valid fare "
            f"({money(impact.cheapest_alone_cents)}), {why}.")


def consultation_prompt(impact: Impact, window_seconds: int) -> str:
    return impact_text(impact, window_seconds) + " May your agent accept this on your behalf?"


def suggestions(candidates: list[Candidate], per_category: int = 3) -> tuple[Suggestion, ...]:
    """Best-rated activity and dinner ideas. Estimates only, never approved terms."""
    result = []
    for category in ("ACTIVITY", "DINNER"):
        ideas = sorted((c for c in candidates if c.category == category and c.title),
                       key=lambda c: (-(c.rating or 0), c.total_cents if c.total_cents is not None else 10**9, c.id))
        seen, distinct = set(), []
        for c in ideas:  # the same place from two searches is one idea
            if c.title.casefold() not in seen:
                seen.add(c.title.casefold())
                distinct.append(c)
        result += [Suggestion(candidate=c.id, category=category, title=c.title, estimate_per_person_cents=c.total_cents,
                              rating=c.rating, source_url=c.source_url) for c in distinct[:per_category]]
    return tuple(result)


def suggestion_text(ideas: tuple[Suggestion, ...]) -> str:
    if not ideas:
        return ""
    def one(s):
        return s.title + (f" (about {money(s.estimate_per_person_cents)} per person)" if s.estimate_per_person_cents else "")
    parts = []
    for category, label in (("ACTIVITY", "Things to do"), ("DINNER", "Group dinner")):
        chosen = [one(s) for s in ideas if s.category == category]
        if chosen:
            parts.append(f"{label}: " + "; ".join(chosen) + ".")
    return " Ideas, not part of what you approve (prices are estimates): " + " ".join(parts)


def explanation(group: GroupSummary, impacts: tuple[Impact, ...], zone: str | None,
                candidates: list[Candidate] | None = None) -> Explanation:
    candidates = candidates or []
    titles = {c.id: c.title for c in candidates if c.title}
    ideas = suggestions(candidates)
    return Explanation(summary=group_text(group, zone, titles) + suggestion_text(ideas), group=group, suggestions=ideas,
        impacts=tuple(ImpactNote(impact=i, text=impact_text(i, group.arrival_window_seconds)) for i in impacts))


def owner_conflict_note(conflict: Conflict, brief: Brief) -> str:
    if conflict.code == "ARRIVAL_TIME":
        return ("No plan fits yet: no flight found for you lands within the arrival times you gave. Allowing other "
                f"arrival times would unblock it; the cheapest such fare is {money(conflict.cheapest_cents)}.")
    if conflict.code == "TOO_MANY_STOPS":
        return ("No plan fits yet: no flight found for you has few enough connections. Allowing a connection would "
                f"unblock it; the cheapest such fare is {money(conflict.cheapest_cents)}.")
    if conflict.code == "TRIP_LIMIT":
        text = (f"No plan fits yet: the cheapest trip found for you is {money(conflict.cheapest_cents)} (your flight plus an "
                f"equal share of the stay and transfer), above your {money(brief.trip_cap_cents)} trip limit. Raising your "
                f"limit to at least {money(conflict.cheapest_cents)} would unblock the plan.")
        if conflict.requirement:
            text += " The group's room requirements raise the stay; your agent has asked privately whether they can be relaxed."
        return text
    if conflict.code == "HARD_LIMIT":
        return (f"No plan fits yet: the cheapest complete fare found for you is {money(conflict.cheapest_cents)}, "
                f"above your {money(brief.hard_cap_cents)} flight limit. Raising your limit to at least "
                f"{money(conflict.cheapest_cents)} would unblock the plan.")
    if conflict.code == "BAGGAGE_UNAVAILABLE":
        return ("No plan fits yet: none of the fares found for you confirm a checked bag. "
                "Dropping the bag requirement, or another search, would unblock the plan.")
    return "No plan fits yet: no fare with a complete price was found for your route."


# Which travellers' own settings a shared conflict comes from; only they are told.
def requirement_holders(conflict: Conflict, briefs: list[Brief]) -> list[str]:
    if conflict.requirement in ("ARRIVAL_TIME", "STOPS"):
        return [conflict.owner]  # their own timing limit, asked of them alone
    if conflict.requirement == "BEDROOMS":
        return [b.owner for b in briefs if b.own_bedroom]
    if conflict.requirement == "RATING":
        top = max((b.stay_min_rating for b in briefs if b.stay_min_rating is not None), default=None)
        return [b.owner for b in briefs if top is not None and b.stay_min_rating == top]
    return []


def requirement_note(conflict: Conflict, brief: Brief) -> str:
    if conflict.code == "TRIP_LIMIT":
        own = "own-room request" if conflict.requirement == "BEDROOMS" else "minimum rating"
        fix = "Sharing a room" if conflict.requirement == "BEDROOMS" else "A lower minimum rating"
        return (f"No plan fits yet: the stay that meets your {own} costs enough that the trip goes over another "
                f"traveller's limit. {fix} would unblock the plan.")
    if conflict.requirement == "BEDROOMS":
        return ("No stay found has enough bedrooms for everyone who asked for their own room, including you. "
                "Sharing a room, or another search, would unblock the plan.")
    return (f"No stay found meets your minimum rating of {brief.stay_min_rating:g}. "
            "Lowering it, or another search, would unblock the plan.")


SHARED_TEXT = {
    ("STAY_UNAVAILABLE", None): "No stay with a complete price was found.",
    ("STAY_UNAVAILABLE", "CAPACITY"): "No stay found sleeps everyone.",
    ("STAY_UNAVAILABLE", "BEDROOMS"): "No stay found has enough bedrooms for the group's room needs.",
    ("STAY_UNAVAILABLE", "RATING"): "No stay found meets the group's minimum rating.",
    ("TRANSFER_UNAVAILABLE", None): "No airport transfer found seats everyone.",
    ("TRIP_LIMIT", None): "Everyone's limits can be met, and everyone can land together, but not both with the options found.",
}


def room_conflict_event(conflicts: tuple[Conflict, ...]) -> dict:
    """Neutral counts by cause; never which traveller is blocked."""
    payload: dict = {"blocked_travellers": dict(sorted(Counter(c.code for c in conflicts if c.owner).items()))}
    arrival = next((c for c in conflicts if c.code == "ARRIVAL_WINDOW"), None)
    lines = []
    for code, count in payload["blocked_travellers"].items():
        who = "1 traveller" if count == 1 else f"{count} travellers"
        lines.append({"HARD_LIMIT": f"{who}: every fare found is above a private limit.",
                      "ARRIVAL_TIME": f"{who}: no flight found lands at a time that works for them.",
                      "TOO_MANY_STOPS": f"{who}: no flight found has few enough connections.",
                      "BAGGAGE_UNAVAILABLE": f"{who}: no fare found confirms a required checked bag.",
                      "TRIP_LIMIT": f"{who}: the cheapest trip found is above a private limit.",
                      "NO_CANDIDATES": f"{who}: no fare with a complete price was found."}[code])
    for conflict in conflicts:
        if not conflict.owner and (conflict.code, conflict.requirement) in SHARED_TEXT:
            lines.append(SHARED_TEXT[(conflict.code, conflict.requirement)])
    if any(c.requirement in {"BEDROOMS", "RATING"} for c in conflicts):
        payload["blocked_requirements"] = sorted({c.requirement for c in conflicts if c.requirement})
    if arrival:
        payload.update(closest_spread_seconds=arrival.closest_spread_seconds, required_seconds=arrival.required_seconds)
        lines.append(f"No combination of flights lands everyone within {duration(arrival.required_seconds)}; "
                     f"the closest is {duration(arrival.closest_spread_seconds)} apart.")
    if payload["blocked_travellers"] or payload.get("blocked_requirements"):
        lines.append("Each affected traveller has a private note from their agent.")
    payload["text"] = "No plan fits yet. " + " ".join(lines)
    return payload


def relax_prompt(conflict: Conflict, brief: Brief) -> str:
    """Owner-only: the exact requirement their agent would set aside for this trip."""
    if conflict.requirement == "ARRIVAL_TIME":
        w = brief.arrival_window or {}
        return (f"No flight found for you lands between {w.get('earliest', '00:00')} and {w.get('latest', '23:59')}; the "
                f"cheapest that doesn't is {money(conflict.cheapest_cents)}. May your agent consider other arrival times "
                "for this trip? You'll still review the final plan.")
    if conflict.requirement == "STOPS":
        return (f"No flight found for you within your stops limit; the cheapest with more stops is "
                f"{money(conflict.cheapest_cents)}. May your agent consider flights with a connection? "
                "You'll still review the final plan.")
    if conflict.requirement == "BEDROOMS":
        why = ("the stay that fits it costs enough to put the trip over another traveller's limit"
               if conflict.code == "TRIP_LIMIT" else "no stay found has enough bedrooms for it")
        return (f"Your own-room request: {why}. May your agent drop it for this trip so you share a room? "
                "You'll still review the final plan.")
    why = ("the stays that meet it cost enough to put the trip over another traveller's limit"
           if conflict.code == "TRIP_LIMIT" else "no stay found meets it")
    return (f"Your minimum stay rating of {brief.stay_min_rating:g}: {why}. May your agent accept lower-rated stays "
            "for this trip? You'll still review the final plan.")
