"""Rules: what a person needs, in one generic language (see
docs/superpowers/specs/2026-10-24-generic-rules-design.md).

A rule names a category, one of its attributes (contracts/attributes.json), an
operator and a value: `FLIGHT.stops lte 0`. Nothing here knows what a flight or
a stop is; the same evaluation runs in the database (spacetimedb/src/rules.ts),
the shortlist and the optimizer's input, and both implementations are checked
against contracts/rule_cases.json.

Evaluation answers SATISFIED, VIOLATED or UNKNOWN (the fact is missing: never
read as fine), or NOT_APPLICABLE when the candidate is another category.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .contracts import ContractError, Record

OPERATORS = ("eq", "neq", "lt", "lte", "gt", "gte", "in", "not_in", "between")
ORDERED = {"money", "int", "number", "epoch", "time", "date"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Rule(Record):
    """One thing a person needs, stored with their own words."""
    owner: str | None = None
    category: str | None = None   # with `attribute`: a rule on each candidate of that category
    attribute: str | None = None
    aggregate: str | None = None  # or a rule on a total (e.g. my_cost)
    operator: Literal["eq", "neq", "lt", "lte", "gt", "gte", "in", "not_in", "between"]
    value: Any
    kind: Literal["hard", "soft"] = "hard"
    importance: int | None = None  # soft rules only: 1-5, how much it matters to them
    tolerance: dict | None = None  # soft rules only: {"limit": …}, partial credit up to there
    state: Literal["active", "set_aside", "kept"] = "active"
    source_quote: str = ""
    source_message_id: int | None = None
    source_profile: str | None = None
    rationale_private: str = ""


class Need(Record):
    """A person's share of what a shared pick must hold (e.g. 1 bedroom of their own)."""
    owner: str
    category: str
    attribute: str
    needs: float = Field(gt=0)


class SearchFact(Record):
    """What to search for, not what to keep (e.g. where someone flies from)."""
    owner: str
    fact: str
    value: Any


class RoomRules(Record):
    """Everyone's rules, needs and search facts in a room, as the planner reads them.
    Private: never checkpointed or shown to the room."""
    rules: tuple[Rule, ...] = ()
    needs: tuple[Need, ...] = ()
    facts: tuple[SearchFact, ...] = ()
    # The trip's own dates (from the catalog's trip_bound marks): they bind every pick.
    bounds: tuple[Rule, ...] = ()

    def binding(self, owner: str | None = None, category: str | None = None) -> list[Rule]:
        """Hard rules in force, active or kept (optionally one owner's, on one category's
        candidates), plus the trip's dates, which bind everyone."""
        own = [r for r in self.rules if r.kind == "hard" and r.state in ("active", "kept")
               and (owner is None or r.owner == owner) and (category is None or r.category == category)]
        return own + [b for b in self.bounds if category is None or b.category == category]

    def within_trip(self, start_date: str | None, end_date: str | None) -> "RoomRules":
        return self.model_copy(update={"bounds": trip_bound_rules(start_date, end_date)})


def stage_decisions(snapshot) -> list[tuple[str, str]]:
    """The organizer's stages as decisions, in order: (category, decision id). A stage the
    catalog repeats (repeats_setting) is that many decisions, or one per full trip day."""
    from datetime import date as _date
    out = []
    for category in snapshot.stages:
        setting = decisions()["categories"][category].get("repeats_setting")
        if setting is None:
            out.append((category, category.lower()))
            continue
        count = getattr(snapshot, setting, None)
        if count is None and snapshot.start_date and snapshot.end_date:
            count = max(1, (_date.fromisoformat(snapshot.end_date) - _date.fromisoformat(snapshot.start_date)).days - 1)
        out += [(category, f"{category.lower()}:{i}") for i in range(1, (count or 1) + 1)]
    return out


def remaining_stages(snapshot) -> dict[str, int]:
    """Decisions still to come after the one being planned: category -> how many."""
    if not snapshot.stages or not snapshot.components:
        return {}
    agreed = {c.decision_id for c in snapshot.commitments}
    left = [(c, d) for c, d in stage_decisions(snapshot) if d not in agreed]
    out: dict[str, int] = {}
    for category, _ in left[1:]:  # the first is the one being planned now
        out[category] = out.get(category, 0) + 1
    return out


def committed_costs(commitments, facts_by_ref: dict, owner_of, members) -> dict[str, int]:
    """What the group's agreed decisions already cost each person, exactly, scaled by n (as
    the database checks budgets: n × their own picks and per-head items, plus whole shared
    totals). A complete plan already contains its parts, so only partial decisions count."""
    n, shapes = len(members), decisions()["categories"]
    out = {m: 0 for m in members}
    for commitment in commitments:
        if commitment.scope in ("COMPLETE_PLAN", "GENERAL_AGREEMENT", "LEGACY"):
            continue
        for ref in commitment.refs:
            facts = facts_by_ref.get(ref)
            if not facts or facts.get("total_cents") is None or facts.get("category") not in shapes:
                continue
            pays = shapes[facts["category"]]["pays"]
            if pays == "each":
                owner = owner_of(facts)
                if owner in out:
                    out[owner] += n * facts["total_cents"]
            else:
                for m in members:
                    out[m] += facts["total_cents"] if pays == "split" else n * facts["total_cents"]
    return out


def search_dates(rules: RoomRules, owner: str, category: str, start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """The dates to search for one person: the trip's dates, narrowed by their own firm rules
    on attributes the catalog marks search_date (e.g. "can't leave before the 21st")."""
    from datetime import timedelta
    shift = lambda day, days: (date.fromisoformat(day) + timedelta(days=days)).isoformat()  # noqa: E731
    out, back = start, end
    spec = attributes()["categories"].get(category, {})
    for rule in rules.binding(owner, category):
        role = spec.get(rule.attribute, {}).get("search_date")
        if rule.owner != owner or role is None:
            continue
        low = {"gte": rule.value, "eq": rule.value, "gt": rule.value and shift(rule.value, 1),
               "between": rule.value[0] if isinstance(rule.value, list) else None}.get(rule.operator)
        high = {"lte": rule.value, "eq": rule.value, "lt": rule.value and shift(rule.value, -1),
                "between": rule.value[1] if isinstance(rule.value, list) else None}.get(rule.operator)
        if role == "outbound" and low is not None:
            out = low if out is None else max(out, low)
        if role == "return" and high is not None:
            back = high if back is None else min(back, high)
    return out, back


def dates_outside_trip(rules: RoomRules, owner: str, category: str, start: str | None, end: str | None) -> set[str]:
    """The person's own date rules that can't fit the trip at all (leaving after it ends,
    back before it starts, or leaving after they must be back): the root of "no plan",
    whatever options were found."""
    out, back = search_dates(rules, owner, category, start, end)
    if not ((end and out and out > end) or (start and back and back < start) or (out and back and out > back)):
        return set()
    spec = attributes()["categories"].get(category, {})
    return {r.attribute for r in rules.binding(owner, category) if r.owner == owner and spec.get(r.attribute, {}).get("search_date")}


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def trip_bound_rules(start_date: str | None, end_date: str | None) -> tuple[Rule, ...]:
    """Each attribute the catalog marks trip_bound, held to the trip's dates: by_end on or
    before the end (arriving), from_start on or after the start (leaving for home). A pick
    without that fact (a one-way flight has no way home) isn't held to it."""
    out = []
    for category, attrs in attributes()["categories"].items():
        for name, spec in attrs.items():
            bound = spec.get("trip_bound")
            if bound == "by_end" and end_date:
                out.append(Rule(category=category, attribute=name, operator="lte", value=end_date,
                                source_quote=f"the trip ends {_day(end_date)}"))
            elif bound == "from_start" and start_date:
                out.append(Rule(category=category, attribute=name, operator="gte", value=start_date,
                                source_quote=f"the trip starts {_day(start_date)}"))
    return tuple(out)


class RuleError(ContractError):
    def __init__(self, detail: str):
        super().__init__("INVALID_RULE")
        self.detail = detail


@lru_cache(maxsize=1)
def attributes() -> dict:
    return json.loads(Path(__file__).with_name("attributes.json").read_text())


@lru_cache(maxsize=1)
def decisions() -> dict:
    return json.loads(Path(__file__).with_name("decisions.json").read_text())


@lru_cache(maxsize=1)
def legacy() -> dict:
    return json.loads(Path(__file__).with_name("legacy_keys.json").read_text())


def attribute_type(rule: Rule) -> str:
    catalog = attributes()
    if rule.aggregate is not None:
        found = catalog["aggregates"].get(rule.aggregate)
    else:
        found = catalog["categories"].get(rule.category or "", {}).get(rule.attribute or "")
    if found is None:
        raise RuleError(f"nothing carries {rule.category}.{rule.attribute or rule.aggregate}")
    return found["type"]


def _scalar(value: Any, kind: str) -> Any:
    """A value of an attribute's type, in comparable form (times as minutes, dates as days)."""
    if kind == "date":
        if isinstance(value, str) and DATE.match(value):
            try:
                return date.fromisoformat(value).toordinal()
            except ValueError:
                pass
        raise RuleError(f"{value!r} isn't a date (YYYY-MM-DD)")
    if kind == "time":
        if not isinstance(value, str) or not TIME.match(value):
            raise RuleError(f"{value!r} isn't a time (HH:MM)")
        hours, minutes = value.split(":")
        return int(hours) * 60 + int(minutes)
    if kind == "bool":
        if not isinstance(value, bool):
            raise RuleError(f"{value!r} isn't true or false")
        return value
    if kind == "text":
        if not isinstance(value, str):
            raise RuleError(f"{value!r} isn't text")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuleError(f"{value!r} isn't a number")
    if kind in ("money", "int", "epoch") and not float(value).is_integer():
        raise RuleError(f"{value!r} isn't a whole number")
    return value


def validate(rule: Rule) -> None:
    """The rule can be checked: its attribute exists and its value fits the operator."""
    if (rule.aggregate is None) == (rule.attribute is None):
        raise RuleError("a rule names one attribute or one aggregate")
    kind = attribute_type(rule)
    op = rule.operator
    if kind in ("bool", "text") and op not in ("eq", "neq", "in", "not_in"):
        raise RuleError(f"{op} doesn't apply to {kind}")
    if op in ("in", "not_in"):
        if not isinstance(rule.value, list) or not rule.value:
            raise RuleError(f"{op} needs a non-empty list")
        for v in rule.value:
            _scalar(v, kind)
    elif op == "between":
        if kind not in ORDERED or not isinstance(rule.value, list) or len(rule.value) != 2:
            raise RuleError("between needs [low, high] of an ordered attribute")
        low, high = (_scalar(v, kind) for v in rule.value)
        if kind != "time" and low > high:
            raise RuleError("between needs low ≤ high (only times may wrap past midnight)")
    else:
        _scalar(rule.value, kind)
    _check_weighting(rule, kind)


def _check_weighting(rule: Rule, kind: str) -> None:
    """How much a preference matters, and how far from it still earns partial credit."""
    if (rule.importance is not None or rule.tolerance is not None) and rule.kind != "soft":
        raise RuleError("only a preference (soft) has an importance or a tolerance")
    if rule.importance is not None and (type(rule.importance) is not int or not 1 <= rule.importance <= 5):
        raise RuleError("importance is a whole number from 1 to 5")
    if rule.tolerance is not None:
        if rule.operator not in ("lt", "lte", "gt", "gte") or not isinstance(rule.tolerance, dict) or set(rule.tolerance) != {"limit"}:
            raise RuleError("a tolerance is {limit} on lt, lte, gt or gte")
        limit, value = _scalar(rule.tolerance["limit"], kind), _scalar(rule.value, kind)
        if (limit <= value) if rule.operator in ("lt", "lte") else (limit >= value):
            raise RuleError("a tolerance limit lies past the preferred value")


def evaluate(rule: Rule, facts: dict) -> str:
    """SATISFIED / VIOLATED / UNKNOWN / NOT_APPLICABLE for one candidate's facts."""
    if rule.aggregate is not None:
        raise RuleError("aggregate rules are evaluated on a whole plan, not one candidate")
    if facts.get("category", rule.category) != rule.category:
        return "NOT_APPLICABLE"
    validate(rule)
    actual = facts.get(rule.attribute)
    if actual is None:
        return "UNKNOWN"
    kind = attribute_type(rule)
    a, op = _scalar(actual, kind), rule.operator
    if op == "in":
        ok = a in [_scalar(v, kind) for v in rule.value]
    elif op == "not_in":
        ok = a not in [_scalar(v, kind) for v in rule.value]
    elif op == "between":
        low, high = (_scalar(v, kind) for v in rule.value)
        ok = low <= a <= high if low <= high else a >= low or a <= high  # a window past midnight
    else:
        b = _scalar(rule.value, kind)
        ok = {"eq": a == b, "neq": a != b, "lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]
    return "SATISFIED" if ok else "VIOLATED"


def from_legacy(owner: str, key: str, row: dict) -> list[Record]:
    """An old named limit as rules, needs or search facts (contracts/legacy_keys.json).
    Coverage markers and notes aren't planning inputs and return nothing."""
    table = legacy()
    if key.startswith(table["kept_prefix"]):
        return []
    spec = table["keys"].get(key)
    if spec is None:
        raise RuleError(f"unknown named limit {key}")
    value = row.get("value")
    if value is None or ("only_when" in spec and value != spec["only_when"]):
        return []  # set aside (cleared), or a "no" that asks for nothing
    common = {"owner": owner, "source_quote": row.get("source_quote", "") or "",
              "rationale_private": row.get("private_rationale") or ""}
    if spec["as"] == "search_fact":
        return [SearchFact(owner=owner, fact=spec["fact"], value=value)]
    if spec["as"] == "need":
        # A need someone hedged ("own room if possible") is a preference, never a must.
        if row.get("hardness", "HARD") != "HARD":
            return []
        return [Need(owner=owner, category=spec["category"], attribute=spec["attribute"], needs=spec["needs"])]
    if spec["as"] != "rule":
        return []
    target = {"aggregate": spec["aggregate"]} if "aggregate" in spec else {"category": spec["category"], "attribute": spec["attribute"]}
    if "value_from" in spec:
        value = [value.get(k) or d for k, d in zip(spec["value_from"], spec["defaults"])]
    elif "value" in spec:
        value = spec["value"]
    out = []
    if row.get("hardness") == "SOFT" and "flexible_limit" in spec:
        # Flexible: what they'd like is a preference with partial credit up to their stated top,
        # or up to the catalog's tolerance if they named none. Only a stated top is also firm.
        top = (row.get("authorized_range") or {}).get("max")
        if top is not None:
            out.append(Rule(**target, operator=spec["operator"], value=top, **common))
        limit = top if top is not None else round(value * spec["soft_tolerance"]) if "soft_tolerance" in spec else None
        tolerance = {"limit": limit} if limit is not None and limit > value else None
        out.append(Rule(**target, operator=spec["operator"], value=value, kind="soft", tolerance=tolerance, **common))
    else:
        firm = row.get("hardness", "HARD") == "HARD" or spec.get("enforce_soft", False)
        out.append(Rule(**target, operator=spec["operator"], value=value, kind="hard" if firm else "soft", **common))
    for rule in out:
        validate(rule)
    return out
