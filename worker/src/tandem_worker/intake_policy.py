"""Deterministic rules for conversational onboarding. The model reads and words
things; these rules decide what counts as said, what's still open, and what
the summary shows. Nothing here fills a gap with a default."""
from __future__ import annotations

import re
from datetime import date

from .contracts import TOPICS, IntakeContext, IntakeExtraction, IntakeItem, IntakeQuestion

MAX_QUESTIONS = 5
MONEY_KEYS = {"flight_budget_total", "trip_budget_total", "no_budget_limit"}
BUDGET_KEYS = {"flight_budget_total", "trip_budget_total"}
# Words that say what a budget covers and how firm it is. Without them a number
# is ambiguous, and ambiguity is a question, never a guess.
TRIP_SCOPE = ("all in", "all-in", "total", "whole trip", "entire trip", "everything", "overall", "including", "in total")
FLIGHT_SCOPE = ("flight", "airfare", "fare", "ticket", "plane")
FIRM = ("max", "under", "no more than", "can't go", "cannot go", "can't do more", "cap", "limit", "at most", "tops",
        "won't go over", "not over", "hard", "absolutely", "not a cent")
FLEX = ("around", "about", "-ish", "ish", "roughly", "flexible", "stretch", "give or take", "approximately", "if needed")
AMOUNT = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k\b|K\b)?")


def amounts(text: str) -> set[float]:
    """Dollar figures mentioned in text: "$1,200", "900", "1.5k"."""
    found = set()
    for number, thousands in AMOUNT.findall(text):
        value = float(number.replace(",", ""))
        found.add(value * 1000 if thousands else value)
    return found


def mentions(text: str, cues: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in cues)


def check_item(item: IntakeItem, said: str) -> str | None:
    """None if `item` is supported by what the traveller said; otherwise why not."""
    if item.source_quote not in said:
        return "QUOTE_NOT_IN_MESSAGE"
    quote, value = item.source_quote, item.value
    if item.key == "origin_airport":
        return None if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else "BAD_VALUE"
    if item.key in BUDGET_KEYS:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return "BAD_VALUE"
        figures = amounts(quote)
        if not any(abs(f * 100 - value) < 1 for f in figures):
            return "NUMBER_NOT_SAID"
        if item.authorized_range is not None:
            top = item.authorized_range.get("max")
            if not isinstance(top, int) or not any(abs(f * 100 - top) < 1 for f in figures):
                return "NUMBER_NOT_SAID"
        return None
    if item.key in {"no_budget_limit", "dates_ok", "baggage_required", "own_bedroom"}:
        return None if isinstance(value, bool) and (value or item.key != "no_budget_limit") else "BAD_VALUE"
    if item.key == "stay_min_rating":
        ok = isinstance(value, (int, float)) and 0 <= value <= 5 and any(abs(f - value) < 0.01 for f in amounts(quote))
        return None if ok else "NUMBER_NOT_SAID"
    if item.key == "arrival_window":
        times = [value.get(k) for k in ("earliest", "latest")] if isinstance(value, dict) else [None]
        well_formed = all(t is None or re.fullmatch(r"\d{2}:\d{2}", str(t)) for t in times) and any(times)
        if not well_formed:
            return "BAD_VALUE"
        # A reading of "no red-eyes" is fine, but it must be shown as a reading.
        return None if item.interpreted or all(t is None or t in quote for t in times) else "NUMBER_NOT_SAID"
    if item.key == "max_stops":
        ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3
        return None if ok and (item.interpreted or str(value) in quote) else "BAD_VALUE"
    if item.key == "trip_note":
        return None if isinstance(value, str) and 0 < len(value) <= 200 else "BAD_VALUE"
    return "UNKNOWN_KEY"


def budget_gap(item: IntakeItem) -> str | None:
    """What a budget still leaves open: what it covers, and how firm it is."""
    quote = item.source_quote
    scope_ok = mentions(quote, TRIP_SCOPE) if item.key == "trip_budget_total" else mentions(quote, FLIGHT_SCOPE)
    firm_ok = mentions(quote, FLEX) if item.hardness == "SOFT" else mentions(quote, FIRM)
    if not scope_ok:
        return "Is that for the flight only, or the whole trip including your share of the stay?"
    if not firm_ok:
        return "Is that a firm limit, or roughly what you'd like to spend?"
    return None


def verify(extraction: IntakeExtraction, ctx: IntakeContext):
    """Keep only what the traveller actually said, and work out each topic's state.
    Returns (items, settings, coverage, reasons)."""
    said = {m.id: m.body for m in ctx.messages if m.sender == "HUMAN"}
    items, reasons = {}, {}
    for item in extraction.items:
        if item.source_message_id not in said or check_item(item, said[item.source_message_id]) is not None:
            continue
        if item.key in BUDGET_KEYS and (gap := budget_gap(item)):
            reasons["money"] = gap
            continue
        items[item.key] = item  # a later statement replaces an earlier one
    settings = extraction.settings
    if settings is not None and (settings.source_message_id not in said or not settings.source_quote
                                 or settings.source_quote not in said.get(settings.source_message_id, "")):
        settings = None
    for unclear in extraction.unclear:
        reasons.setdefault(unclear.topic, unclear.reason)

    have = set(items) | set(ctx.confirmed_keys)
    coverage = {
        "origin": "ANSWERED" if "origin_airport" in have else "OPEN",
        "dates": "ANSWERED" if have & {"dates_ok", "arrival_window"} else "OPEN",
        "money": "ANSWERED" if have & MONEY_KEYS else "OPEN",
        "settings": "ANSWERED" if ctx.preferences_confirmed or (
            settings is not None and settings.speak_up_for_me is not None and settings.ask_me_first is not None) else "OPEN",
    }
    for topic, reason in reasons.items():
        if coverage.get(topic) != "ANSWERED":
            coverage[topic] = "UNCLEAR"
    return tuple(items.values()), settings, coverage, reasons


def open_topics(coverage: dict) -> list[str]:
    return [t for t in TOPICS if coverage.get(t) in ("OPEN", "UNCLEAR")]


def _dates(brief) -> str:
    start, end = date.fromisoformat(brief.start_date), date.fromisoformat(brief.end_date)
    return f"{start:%b} {start.day}–{end.day}" if start.month == end.month else f"{start:%b} {start.day} – {end:%b} {end.day}"


def opening_question(ctx: IntakeContext) -> str:
    with_whom = f" with {', '.join(ctx.others[:-1]) + ' and ' + ctx.others[-1] if len(ctx.others) > 1 else ctx.others[0]}" if ctx.others else ""
    return (f"Hi {ctx.display_name or ctx.owner}! {ctx.brief.destination}, {_dates(ctx.brief)}{with_whom}. Where would you fly from, and is there "
            "anything about this trip that's a must-have or a deal-breaker for you?")


FALLBACK = {
    "origin": "Where will you be flying from?",
    "dates": "Do {dates} work for you, and is there a time you'd rather not land?",
    "money": "What would you like to keep this trip to: for the flight or everything, and is that firm or flexible?",
    "settings": ("If a plan breaks one of your limits, should I tell the group (never why) or just you? "
                 "And should I check with you before agreeing to pay more?"),
}


def fallback_question(topics: list[str], ctx: IntakeContext, reasons: dict) -> IntakeQuestion:
    """Our own wording for the top open topics: two if they fit in one short message, else one."""
    parts = [reasons.get(t) or FALLBACK[t].format(dates=_dates(ctx.brief)) for t in topics[:2]]
    if len(" ".join(parts)) > 300:
        parts, topics = parts[:1], topics[:1]
    return IntakeQuestion(text=" ".join(parts), topics=tuple(topics[:2]))


def question_ok(question: IntakeQuestion, ctx: IntakeContext) -> bool:
    """One short private question, about open topics, inventing no amounts."""
    text = question.text.strip()
    if not text.endswith("?") or len(text) > 300:
        return False
    known = amounts(" ".join(m.body for m in ctx.messages)) | {float(ctx.brief.start_date[8:]), float(ctx.brief.end_date[8:])}
    return amounts(text) <= known


def patch_json(item: IntakeItem) -> dict:
    patch = {"key": item.key, "value": item.value, "hardness": item.hardness, "unit": "USD_cents" if item.key in BUDGET_KEYS else "",
             "source_quote": item.source_quote}
    if item.authorized_range is not None:
        patch["authorized_range"] = item.authorized_range
    if item.private_rationale:
        patch["private_rationale"] = item.private_rationale
    return patch


def money(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def describe(item: IntakeItem) -> str:
    v = item.value
    if item.key == "origin_airport":
        return f"Flying from {v}"
    if item.key in BUDGET_KEYS:
        what = "the whole trip" if item.key == "trip_budget_total" else "your flight"
        if item.hardness == "SOFT":
            top = (item.authorized_range or {}).get("max")
            return f"Around {money(v)} for {what}" + (f", up to {money(top)} if it helps" if top else ", flexible")
        return f"Up to {money(v)} for {what}, firm"
    text = {
        "no_budget_limit": "No budget limit",
        "dates_ok": "The dates work for you" if v else "The dates don't work as they are",
        "baggage_required": "You need a checked bag" if v else "No checked bag needed",
        "own_bedroom": "You'd like your own room" if v else "Happy to share a room",
        "stay_min_rating": f"Stays rated {v} or higher",
        "max_stops": "Nonstop only" if v == 0 else f"At most {v} stop{'s' if v != 1 else ''}",
        "trip_note": f"Noted, not used in planning yet: {v}",
    }.get(item.key)
    if item.key == "arrival_window":
        early, late = v.get("earliest"), v.get("latest")
        text = f"Land {'between ' + early + ' and ' + late if early and late else 'after ' + early if early else 'before ' + late}"
    return text or item.key


def summary_text(items: tuple[IntakeItem, ...], settings, ctx: IntakeContext, missing_money: bool) -> str:
    lines = ["Here's what I've got. Confirm, or tell me what to change:"]
    for item in items:
        if item.key == "origin_airport" and "origin_airport" in ctx.confirmed_keys:
            lines.append(f"• {describe(item)} (confirmed)")
            continue
        reading = " (my reading of" if item.interpreted else " (you said"
        lines.append(f"• {describe(item)}{reading} “{item.source_quote}”)")
    if settings is not None and not ctx.preferences_confirmed:
        lines.append("• How I'll act for you: " + (
            "I'll tell the group when a plan breaks one of your limits (never why)" if settings.speak_up_for_me
            else "I'll tell only you when a plan breaks one of your limits") + ", and " + (
            "I'll check with you before agreeing to pay more." if settings.ask_me_first
            else "I'll accept small trade-offs within your limits without asking."))
    if missing_money:
        lines.append("• Still needed: a budget, or say there's no limit. I won't plan without it.")
    lines.append("Anything you didn't mention, I'll ask about only if a plan depends on it.")
    return "\n".join(lines)
