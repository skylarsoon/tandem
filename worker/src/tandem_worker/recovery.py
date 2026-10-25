"""Saying no to every option: what happens next.

When someone declines a compromise, their agent remembers the option and why it fell
short for them (the preferences of theirs it missed, and their reason if they gave
one). A declined option is never put to them again unless something changed in its
favour: a preference it missed now holds, its price dropped, or they took it back.

When they've said no to everything left for a decision, their agent asks one question
with concrete ways forward, built from what they declined (never a fixed list):

  change  their preferences so a declined option fits ("fare at most $1,350"), kept
          as confirmed changes like any other (patches), and the room replans;
  accept  a declined option after all, as it is;
  search  again, differently (leaving a day later, or a wider search);
  pause   the decision until something they've said changes.

Every answer moves things on and is remembered, so nothing is offered twice for the
same reason, and the rounds are capped: after MAX_ROUNDS only pausing is left, and
they're told plainly that nothing found works for what they said. Hard limits are
never relaxed: a declined option already met them, and every change is one they pick.
Private to the person and their own agent; the room only ever hears counts."""
from __future__ import annotations

import json
from datetime import date, timedelta

from .contracts import Declined, RecoveryAnswer, digest
from .intake_policy import day, describe_rule, money
from .rules import ORDERED, RoomRules, Rule, attribute_type, attributes, comparable, evaluate, importance, search_dates

MAX_ROUNDS = 3
MAX_TRADE_OFFS = 3
# Preferences a single new value can loosen ("at most $800" → "at most $1,350").
LOOSENED = {"lte", "gte"}
NOUNS = {"FLIGHT": "flight", "STAY": "place to stay", "TRANSFER": "airport transfer", "ACTIVITY": "activity",
         "DINNER": "dinner"}
PAUSE = {"id": "pause", "kind": "pause", "label": "Pause this for now; I'll pick it up when something you've told me changes"}


def option_key(candidate: dict) -> str:
    """The same option across searches (each search gives it a new id), without its price."""
    ident = candidate.get("flight_identity") or candidate.get("source_url") or candidate["id"]
    return "|".join(str(x) for x in (candidate.get("category", "FLIGHT"), candidate.get("owner") or "", ident,
                                     candidate.get("baggage_included")))


def option_text(candidate: dict) -> str:
    """"HA1 LAX-HNL (nonstop, $1,350)"."""
    name = candidate.get("title") or NOUNS.get(candidate.get("category", ""), "option")
    bits = []
    stops = candidate.get("stops")
    if stops is not None:
        bits.append("nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}")
    if candidate.get("total_cents") is not None:
        bits.append(money(candidate["total_cents"]))
    return f"{name} ({', '.join(bits)})" if bits else name


def _slot(rule) -> tuple:
    if isinstance(rule, dict):
        return rule.get("category"), rule.get("attribute"), rule.get("operator")
    return rule.category, rule.attribute, rule.operator


def unmet(rules: RoomRules, owner: str, candidate: dict) -> list[Rule]:
    """Their preferences this one option doesn't meet (those an option can be checked against)."""
    return [r for r in rules.soft(owner, candidate.get("category", "FLIGHT"))
            if r.attribute and evaluate(r, candidate) == "VIOLATED"]


def remember(rules: RoomRules, owner: str, decision_id: str, candidate: dict) -> dict:
    """What a consultation is about, kept with the question so a no is remembered."""
    return {"decision_id": decision_id, "key": option_key(candidate), "price_cents": candidate.get("total_cents"),
            "unmet": [{"category": r.category, "attribute": r.attribute, "operator": r.operator, "value": r.value}
                      for r in unmet(rules, owner, candidate)]}


def lifted(declined: Declined, candidate: dict, rules: RoomRules, answers: list[RecoveryAnswer]) -> bool:
    """Something changed in this option's favour since they said no to it."""
    price = candidate.get("total_cents")
    if price is not None and declined.price_cents is not None and price < declined.price_cents:
        return True
    if any(a.owner == declined.owner and a.decision_id == declined.decision_id and a.seq > declined.seq
           and a.choice.get("kind") == "accept" and a.choice.get("option") == declined.key for a in answers):
        return True
    for missed in declined.unmet:
        now = [r for r in rules.soft(declined.owner, missed["category"]) if _slot(r) == _slot(missed)]
        if not now or all(evaluate(r, candidate) == "SATISFIED" for r in now):
            return True
    return False


def still_declined(declines: list[Declined], answers: list[RecoveryAnswer], candidates: list[dict],
                   rules: RoomRules, decision_id: str) -> set[str]:
    """Ids of the options someone said no to for this decision that nothing has changed in favour of."""
    out = set()
    for c in candidates:
        key = option_key(c)
        if any(d.decision_id == decision_id and d.key == key and c.get("owner") in (None, d.owner)
               and not lifted(d, c, rules, answers) for d in declines):
            out.add(c["id"])
    return out


def rules_ref(rules: RoomRules, owner: str) -> str:
    """Their own rules, needs and search facts as they stand now."""
    mine = [*(r for r in rules.rules if r.owner == owner), *(n for n in rules.needs if n.owner == owner),
            *(f for f in rules.facts if f.owner == owner)]
    return digest(sorted(digest(x.model_dump(mode="json")) for x in mine))[:20]


def rounds(answers: list[RecoveryAnswer], owner: str, decision_id: str) -> list[RecoveryAnswer]:
    return sorted((a for a in answers if a.owner == owner and a.decision_id == decision_id), key=lambda a: a.seq)


def paused(answers: list[RecoveryAnswer], rules: RoomRules, owner: str, decision_id: str) -> bool:
    """They asked to pause this decision and haven't changed anything they've told their agent since."""
    mine = rounds(answers, owner, decision_id)
    return bool(mine) and mine[-1].choice.get("kind") == "pause" and mine[-1].rules_ref == rules_ref(rules, owner)


def choices(owner: str, decision_id: str, category: str, declines: list[Declined], answers: list[RecoveryAnswer],
            candidates: list[dict], excluded: set[str], rules: RoomRules,
            start: str | None, end: str | None) -> tuple[list[dict], bool]:
    """The ways forward for someone who said no to every option left, and whether anything
    besides pausing is left (if not, they're told plainly that nothing found works)."""
    mine = rounds(answers, owner, decision_id)
    out = []
    if len(mine) < MAX_ROUNDS:
        out += _trade_offs(owner, decision_id, declines, candidates, excluded, rules)
        ref = rules_ref(rules, owner)
        tried = {json.dumps(a.choice.get("search"), sort_keys=True) for a in mine
                 if a.choice.get("kind") == "search" and a.rules_ref == ref}
        out += [c for c in _searches(owner, category, rules, start, end)
                if json.dumps(c["search"], sort_keys=True) not in tried]
    return [*out, PAUSE], bool(out)


def _loosened(rule: Rule, value) -> Rule:
    kind = attribute_type(rule)
    tolerance = rule.tolerance
    if tolerance and tolerance.get("limit") is not None:
        beyond = comparable(tolerance["limit"], kind) - comparable(value, kind)
        tolerance = tolerance if (beyond > 0 if rule.operator == "lte" else beyond < 0) else None
    return rule.model_copy(update={"value": value, "tolerance": tolerance})


def _how_far(rule: Rule, value) -> float:
    kind = attribute_type(rule)
    gap = comparable(value, kind) - comparable(rule.value, kind)
    return gap if rule.operator == "lte" else -gap


def _patch(rules: RoomRules, owner: str, new: Rule, label: str) -> dict:
    """The change as a confirmed patch would carry it: their old named limit if the rule
    mirrors one (so both stay the same), otherwise the rule itself."""
    if new.legacy_key:
        patch = {"key": new.legacy_key, "value": new.value, "hardness": "SOFT",
                 "unit": "USD_cents" if attribute_type(new) == "money" else "", "source_quote": label}
        top = next((r for r in rules.binding(owner, new.category) if r.owner == owner and r.legacy_key == new.legacy_key), None)
        if top is not None:  # a flexible limit with a stated top keeps it
            patch["authorized_range"] = {"max": top.value}
        return patch
    rule = {"category": new.category, "attribute": new.attribute, "operator": new.operator, "value": new.value, "kind": "soft"}
    if new.importance is not None:
        rule["importance"] = new.importance
    if new.tolerance is not None:
        rule["tolerance"] = new.tolerance
    return {"rule": rule, "source_quote": label}


def _trade_offs(owner, decision_id, declines, candidates, excluded, rules) -> list[dict]:
    """Per kind of change, the smallest one that brings back an option they declined; and
    declined options that missed nothing they said (only cost more), to take after all."""
    current = {}
    for c in sorted(candidates, key=lambda c: c["id"]):
        if c["id"] in excluded and c.get("owner") in (None, owner):
            current.setdefault(option_key(c), c)
    changes, accepts = {}, []
    for key in sorted({d.key for d in declines if d.owner == owner and d.decision_id == decision_id}):
        c = current.get(key)
        if c is None:
            continue  # not found again: nothing to bring back
        misses = unmet(rules, owner, c)
        if not misses:
            accepts.append(c)
            continue
        if not all(r.operator in LOOSENED and attribute_type(r) in ORDERED for r in misses):
            continue
        slots = tuple(sorted(_slot(r) for r in misses))
        size = (tuple(_how_far(r, c[r.attribute]) for r in sorted(misses, key=_slot)), c.get("total_cents") or 0)
        if slots not in changes or size < changes[slots][0]:
            changes[slots] = (size, c, misses)
    ranking = rules.ranking.get(owner, ())
    ordered = sorted(changes.values(), key=lambda x: (sum(importance(ranking, attribute=r.attribute) for r in x[2]),
                                                      x[1].get("total_cents") or 0))
    out = []
    for n, (_, c, misses) in enumerate(ordered, 1):
        new = [_loosened(r, c[r.attribute]) for r in misses]
        label = f"{'; '.join(describe_rule(r) for r in new)}: that brings back {option_text(c)}"
        out.append({"id": f"change-{n}", "kind": "change", "option": option_key(c),
                    "patches": [_patch(rules, owner, r, label) for r in new], "label": label})
    for n, c in enumerate(sorted(accepts, key=lambda c: c.get("total_cents") or 0), 1):
        out.append({"id": f"accept-{n}", "kind": "accept", "option": option_key(c), "label": f"Take {option_text(c)} after all"})
    return out[:MAX_TRADE_OFFS]


def _searches(owner, category, rules, start, end) -> list[dict]:
    out = []
    spec = attributes()["categories"].get(category, {})
    if start and any(s.get("search_date") == "outbound" for s in spec.values()):
        first, back = search_dates(rules, owner, category, start, end)
        later = (date.fromisoformat(first) + timedelta(days=1)).isoformat()
        if back is None or later < back:
            out.append({"id": "later", "kind": "search", "search": {"shift_days": 1},
                        "label": f"Look at leaving a day later ({day(later)})"})
    out.append({"id": "wider", "kind": "search", "search": {"wider": True}, "label": "Look for more options (a wider search)"})
    return out


def fallback_prompt(category: str, declined: int, left: bool) -> str:
    noun = NOUNS.get(category, "plan")
    if not left:
        return (f"None of the options I found for your {noun} work for what you've told me, and I've tried what I can. "
                "Shall I pause this until something changes, or tell me what you'd change?")
    return (f"You said no to each of the {declined} options I asked you about for your {noun}. "
            "Which of these would you go with?")


def check_prompt(raw, offered: list[dict]):
    """The model's wording is used only if it's a short question and recommends an offered choice (or none)."""
    ids = {c["id"] for c in offered}
    text = (raw.prompt or "").strip()
    if not text or len(text) > 700 or "?" not in text or (raw.recommended is not None and raw.recommended not in ids):
        raise ValueError("INVALID_RECOVERY_PROMPT")
    return text, raw.recommended
