"""Deterministic rules for conversational onboarding. The model reads and words
things; these rules decide what counts as said, what's still open, and what
the summary shows. Nothing here fills a gap with a default."""
from __future__ import annotations

import re
from datetime import date

from pydantic import ValidationError

from .contracts import TOPICS, IntakeContext, IntakeExtraction, IntakeItem, IntakeQuestion
from .rules import RANKINGS, RELATIONS, Rule, RuleError, attribute_type, attributes, validate

MAX_QUESTIONS = 5
MONEY_KEYS = {"flight_budget_total", "trip_budget_total", "no_budget_limit"}
BUDGET_KEYS = {"flight_budget_total", "trip_budget_total"}
# Words that say what a budget covers and how firm it is. Without them a number
# is ambiguous, and ambiguity is a question, never a guess.
TRIP_SCOPE = ("all in", "all-in", "total", "whole trip", "entire trip", "everything", "overall", "including", "in total")
FLIGHT_SCOPE = ("flight", "airfare", "fare", "ticket", "plane")
FIRM = ("max", "under", "no more than", "can't go", "cannot go", "can't do more", "cap", "limit", "at most", "tops",
        "won't go over", "not over", "hard", "absolutely", "not a cent", "exceed")
FLEX = ("around", "about", "-ish", "ish", "roughly", "flexible", "stretch", "give or take", "approximately", "if needed")
# A requirement needs a firm word; a hedge ("would be nice", "flexible") makes it a
# preference. Recording it as a requirement would be assuming; dropping it would lose it.
NEEDS = ("must", "need", "non-negotiable", "have to", "require", "can't do without", "essential", "deal-breaker")
HEDGES = ("would be nice", "nice to have", "flexible", "if possible", "ideally", "prefer", "don't mind", "not a big deal")
NONSTOP = ("nonstop", "non-stop", "direct", "no connection", "no layover", "no stops")
AMOUNT = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k\b|K\b)?")


NUMBER_WORDS = {"no": 0, "zero": 0, "one": 1, "a single": 1, "two": 2, "three": 3}


def amounts(text: str) -> set[float]:
    """Dollar figures mentioned in text: "$1,200", "900", "1.5k"."""
    found = set()
    for number, thousands in AMOUNT.findall(text):
        value = float(number.replace(",", ""))
        found.add(value * 1000 if thousands else value)
    return found


def counts(text: str) -> set[float]:
    """Numbers mentioned in text, small ones in words too ("no more than one stop")."""
    lowered = f" {text.lower()} "
    return amounts(text) | {float(v) for w, v in NUMBER_WORDS.items() if f" {w} " in lowered}


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
        # The amount must be in their words; a stretch figure may come later in the same message.
        if not any(abs(f * 100 - value) < 1 for f in amounts(quote)):
            return "NUMBER_NOT_SAID"
        if item.authorized_range is not None:
            top = item.authorized_range.get("max")
            if not isinstance(top, int) or not any(abs(f * 100 - top) < 1 for f in amounts(said)):
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
        stated = float(value) in counts(quote) or (value == 0 and mentions(quote, NONSTOP))
        return None if ok and (item.interpreted or stated) else "BAD_VALUE"
    if item.key == "trip_note":
        return None if isinstance(value, str) and 0 < len(value) <= 200 else "BAD_VALUE"
    if item.key == "rule":
        return _check_rule(item, said)
    return "UNKNOWN_KEY"


def _check_rule(item: IntakeItem, said: str) -> str | None:
    """A rule must be checkable (catalog attribute, fitting value) and said: every
    number, time or word in its value appears in their words (a reading, e.g.
    "nonstop" as 0 connections, is allowed but shown as a reading)."""
    try:
        rule = Rule.model_validate({**(item.rule or {}), "kind": "hard" if item.hardness == "HARD" else "soft"})
        validate(rule)
    except (RuleError, ValidationError):
        return "BAD_VALUE"
    kind, quote = attribute_type(rule), item.source_quote
    if rule.operator in RANKINGS:
        return None  # "as short as possible" names no number; the quote is the evidence
    if rule.operator in RELATIONS:  # whose: their name anywhere in that message ("Alex and I... the same flight")
        return None if str(rule.value).lower() in said.lower() else "NUMBER_NOT_SAID"
    for v in rule.value if isinstance(rule.value, list) else [rule.value]:
        if kind == "bool":
            continue
        if kind == "money":
            stated = any(abs(f * 100 - v) < 1 for f in amounts(quote))
        elif kind == "date":  # "November 21" or "the 21st" states 2026-11-21's day
            stated = float(date.fromisoformat(v).day) in amounts(quote)
        elif kind in ("int", "number", "epoch"):
            stated = any(abs(f - v) < 0.01 for f in counts(quote))
        else:
            stated = str(v).lower() in quote.lower()
        if not stated and not item.interpreted:
            return "NUMBER_NOT_SAID"
    return None


def soften(item: IntakeItem, said: str) -> IntakeItem:
    """Anything said with a hedge and no firm word ("nonstop would be nice") is kept
    as a preference, and "as short as possible" always is one. Budgets have their own
    firmness check (budget_gap)."""
    if item.key in MONEY_KEYS or item.hardness != "HARD":
        return item
    if item.key == "rule" and (item.rule or {}).get("operator") in RANKINGS + RELATIONS:
        return item.model_copy(update={"hardness": "SOFT"})  # a ranking or someone else's pick: only ever a wish
    if mentions(item.source_quote, HEDGES) and not mentions(said, NEEDS):
        return item.model_copy(update={"hardness": "SOFT"})
    return item


def budget_gap(item: IntakeItem, said: str) -> str | None:
    """What a budget still leaves open: what it covers, and how firm it is.
    Cues count anywhere in the message they said it in."""
    quote = said
    scope_ok = mentions(quote, TRIP_SCOPE) if item.key == "trip_budget_total" else mentions(quote, FLIGHT_SCOPE)
    firm_ok = mentions(quote, FLEX) if item.hardness == "SOFT" else mentions(quote, FIRM)
    if item.hardness == "HARD" and re.search(r"\bfirm\b", quote, re.I):
        firm_ok = True
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
        if item.source_message_id not in said:
            continue
        item = soften(item, said[item.source_message_id])
        problem = check_item(item, said[item.source_message_id])
        if problem == "NUMBER_NOT_SAID" and item.key in BUDGET_KEYS and item.authorized_range is not None:
            # Keep the budget they stated; ask about the stretch instead of dropping both.
            item = item.model_copy(update={"authorized_range": None})
            problem = check_item(item, said[item.source_message_id])
        if problem is not None:
            if item.key in BUDGET_KEYS and problem == "NUMBER_NOT_SAID":
                figures = sorted(amounts(said[item.source_message_id]))
                if figures:  # tell them what we heard rather than silently dropping it
                    reasons["money"] = (f"Just to be sure I have your budget right: is it ${figures[0]:,.0f} for "
                                        "the whole trip or the flight only, and is that firm or flexible?")
            continue
        # A budget's amount can be followed by an answer about scope/firmness.
        # Use only subsequent answers, stopping at a new amount, and never use
        # the agent's questions as evidence of what the traveller agreed to.
        budget_said = said[item.source_message_id]
        if item.key in BUDGET_KEYS:
            after_source = False
            for message in ctx.messages:
                if message.id == item.source_message_id:
                    after_source = True
                    continue
                if not after_source or message.sender != "HUMAN":
                    continue
                if amounts(message.body):
                    break
                budget_said += " " + message.body
        if item.key in BUDGET_KEYS and (gap := budget_gap(item, budget_said)):
            reasons["money"] = gap
            continue
        if item.key == "max_stops" and str(item.value) not in item.source_quote:
            item = item.model_copy(update={"interpreted": True})  # "nonstop" read as 0 stops: shown as a reading
        # A later statement replaces an earlier one about the same thing.
        slot = item.key if item.key != "rule" else (
            f"rule:{item.rule.get('category')}.{item.rule.get('attribute') or item.rule.get('aggregate')}:{item.rule.get('operator')}"
            f":{item.hardness}")
        items[slot] = item
    settings = extraction.settings
    if settings is not None and (settings.source_message_id not in said or not settings.source_quote
                                 or settings.source_quote not in said.get(settings.source_message_id, "")):
        settings = None
    for unclear in extraction.unclear:
        # Only a real question to them, about something they actually said, reaches them;
        # a note about them ("Maya didn't say…") is never sent.
        if speakable(unclear.reason, ctx):
            reasons.setdefault(unclear.topic, unclear.reason)

    # The extractor correctly omits an ambiguous money item. Still acknowledge
    # an explicit budget amount even if it omitted the clarification as well.
    if not any(i.key in MONEY_KEYS or (i.key == "rule" and _is_money(i)) for i in items.values()):
        for message in reversed(ctx.messages):
            if message.sender != "HUMAN" or not re.search(r"\bbudget\b", message.body, re.I):
                continue
            figures = amounts(message.body)
            if len(figures) == 1:
                amount = money(round(next(iter(figures)) * 100))
                reasons.setdefault("money", f"Is {amount} for the flight only or the whole trip, and is it a firm limit or flexible?")
            break

    have = set(items) | set(ctx.confirmed_keys)
    if any(i.key == "rule" and _is_money(i) for i in items.values()):
        have.add("trip_budget_total")  # any money limit covers money
    if any(i.key == "rule" and _rule_type(i) == "date" for i in items.values()):
        have.add("dates_ok")  # their own dates answer whether the trip's dates work
    coverage = {
        "origin": "ANSWERED" if "origin_airport" in have else "OPEN",
        "dates": "ANSWERED" if have & {"dates_ok", "arrival_window"} else "OPEN",
        "money": "ANSWERED" if have & MONEY_KEYS else "OPEN",
        "settings": "ANSWERED" if ctx.preferences_confirmed or (
            settings is not None and settings.speak_up_for_me is not None and settings.ask_me_first is not None) else "OPEN",
    }
    dates_said = items.get("dates_ok")
    if dates_said is not None and dates_said.value is False and not any(
            i.key == "rule" and _rule_type(i) == "date" for i in items.values()):
        # "The dates don't work" isn't an answer yet: which dates do?
        coverage["dates"] = "UNCLEAR"
        reasons.setdefault("dates", "Which dates can you do: what's the earliest you can leave, and when do you need to be back?")
    for topic, reason in reasons.items():
        if coverage.get(topic) != "ANSWERED":
            coverage[topic] = "UNCLEAR"
    return tuple(items.values()), settings, coverage, reasons


def speakable(text: str, ctx: IntakeContext) -> bool:
    text = text.strip()
    names = {n.lower() for n in (ctx.owner, ctx.display_name) if n}
    third_person = re.search(r"\b(they|she|he|them|her|his|ask|traveller|traveler|user)\b", text, re.I)
    return (text.endswith("?") and len(text) <= 200 and not third_person
            and not any(re.search(rf"\b{re.escape(n)}\b", text.lower()) for n in names))


def open_topics(coverage: dict) -> list[str]:
    return [t for t in TOPICS if coverage.get(t) in ("OPEN", "UNCLEAR")]


def _dates(brief) -> str:
    start, end = date.fromisoformat(brief.start_date), date.fromisoformat(brief.end_date)
    return f"{start:%b} {start.day}–{end.day}" if start.month == end.month else f"{start:%b} {start.day} – {end:%b} {end.day}"


def opening_question(ctx: IntakeContext, usual_airport: str | None = None) -> str:
    with_whom = f" with {', '.join(ctx.others[:-1]) + ' and ' + ctx.others[-1] if len(ctx.others) > 1 else ctx.others[0]}" if ctx.others else ""
    where = (f"Flying from {usual_airport} like usual? Tap confirm, or tell me where you're flying from. And is there "
             if usual_airport else "Where would you fly from, and is there ")
    return (f"Hi {ctx.display_name or ctx.owner}! {ctx.brief.destination}, {_dates(ctx.brief)}{with_whom}. {where}"
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
    if item.key == "rule":
        return {"rule": {**item.rule, "kind": "hard" if item.hardness == "HARD" else "soft"}, "source_quote": item.source_quote}
    patch = {"key": item.key, "value": item.value, "hardness": item.hardness, "unit": "USD_cents" if item.key in BUDGET_KEYS else "",
             "source_quote": item.source_quote}
    if item.authorized_range is not None:
        patch["authorized_range"] = item.authorized_range
    if item.private_rationale:
        patch["private_rationale"] = item.private_rationale
    return patch


def _is_money(item: IntakeItem) -> bool:
    return _rule_type(item) == "money"


def _rule_type(item: IntakeItem) -> str | None:
    try:
        return attribute_type(Rule.model_validate({**(item.rule or {}), "kind": "hard"}))
    except (RuleError, ValidationError):
        return None


WORDS = {"lte": "at most", "lt": "under", "gte": "at least", "gt": "over", "eq": "is", "neq": "isn't",
         "in": "one of", "not_in": "none of", "between": "between", "contains": "include"}


def describe_rule(rule: Rule) -> str:
    """A rule in plain words, from the catalog's labels: "Stay: rating at least 4.5"."""
    catalog = attributes()
    spec = catalog["aggregates"][rule.aggregate] if rule.aggregate else catalog["categories"][rule.category][rule.attribute]
    show = money if spec["type"] == "money" else day if spec["type"] == "date" else str
    if rule.operator == "same_as":
        text = f"{spec.get('same_as', 'the same ' + spec['label'] + ' as')} {rule.value}"
    elif rule.operator in RANKINGS:
        timed = spec["type"] in ("epoch", "time", "date")
        way = ("early" if timed else "low") if rule.operator == "lowest" else ("late" if timed else "high")
        text = f"{spec['label']} {spec.get(rule.operator, f'as {way} as possible')}"
    elif spec["type"] == "bool":
        must = "" if rule.kind == "soft" else "must "
        text = must + ("have " if rule.value else "not have ") + spec["label"]
    elif rule.operator == "between":
        text = f"{spec['label']} between {show(rule.value[0])} and {show(rule.value[1])}"
    elif rule.operator in ("lte", "lt") and rule.value == (0 if rule.operator == "lte" else 1) and spec["type"] == "int":
        text = f"no {spec['label']}"  # "at most 0 connections" is "no connections"
    elif isinstance(rule.value, list):
        text = f"{spec['label']} {WORDS[rule.operator]} {', '.join(show(v) for v in rule.value)}"
    else:
        text = f"{spec['label']} {WORDS[rule.operator]} {show(rule.value)}"
    return text[0].upper() + text[1:] if rule.aggregate else f"{rule.category.title()}: {text}"


def day(iso: str) -> str:
    """2026-11-25 as "Nov 25"."""
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def money(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def describe(item: IntakeItem) -> str:
    text = _describe(item)
    ranking = item.key == "rule" and (item.rule or {}).get("operator") in RANKINGS  # already a wish
    if item.hardness == "SOFT" and item.key not in BUDGET_KEYS and not ranking:
        return f"If possible: {text[0].lower()}{text[1:]}"  # a preference, weighed but never a must
    return text


def _describe(item: IntakeItem) -> str:
    soft = item.hardness == "SOFT"
    if item.key == "rule":
        return describe_rule(Rule.model_validate({**item.rule, "kind": "soft" if soft else "hard"}))
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
        "baggage_required": ("A checked bag" if soft else "You need a checked bag") if v else "No checked bag needed",
        "own_bedroom": "You'd like your own room" if v else "Happy to share a room",
        "stay_min_rating": f"Stays rated {v} or higher",
        "max_stops": ("Nonstop" if soft else "Nonstop only") if v == 0 else f"At most {v} stop{'s' if v != 1 else ''}",
        "trip_note": f"Noted, not used in planning yet: {v}",
    }.get(item.key)
    if item.key == "arrival_window":
        early, late = v.get("earliest"), v.get("latest")
        text = f"Land {'between ' + early + ' and ' + late if early and late else 'after ' + early if early else 'before ' + late}"
    return text or item.key


def priorities_text(profile: dict) -> str:
    """Their own priority ranking (profile question C03), in their words' order."""
    from .profile_policy import catalog
    ranked = (profile.get("C03") or {}).get("ranked") or []
    question = next((q for q in catalog()["questions"] if q["id"] == "C03"), None)
    labels = {o["id"]: o["label"].lower() for o in (question or {}).get("options", [])}
    names = [labels[r] for r in ranked if r in labels]
    if not names:
        return ""
    return names[0] + " first" + (", then " + ", ".join(names[1:]) if names[1:] else "")


def summary_text(items: tuple[IntakeItem, ...], settings, ctx: IntakeContext, missing_money: bool,
                 suggested=(), settings_from_profile: bool = False) -> str:
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
            "I'll check with you before agreeing to pay more" if settings.ask_me_first
            else "I'll accept small trade-offs within your limits without asking")
            + (" (that part from your profile)." if settings_from_profile else "."))
    for suggestion in suggested:
        lines.append(f"• {suggestion.text} (from your profile; for this trip too?)")
    weighing = priorities_text(ctx.profile)
    if weighing:
        lines.append(f"• When options trade off, I'll weigh them your way: {weighing} (from your profile).")
    if missing_money:
        lines.append("• Still needed: a budget, or say there's no limit. I won't plan without it.")
    lines.append("Anything you didn't mention, I'll ask about only if a plan depends on it.")
    return "\n".join(lines)
