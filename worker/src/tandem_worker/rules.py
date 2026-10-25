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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .contracts import ContractError, Record

OPERATORS = ("eq", "neq", "lt", "lte", "gt", "gte", "in", "not_in", "between")
ORDERED = {"money", "int", "number", "epoch", "time"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Rule(Record):
    """One thing a person needs, stored with their own words."""
    owner: str | None = None
    category: str | None = None   # with `attribute`: a rule on each candidate of that category
    attribute: str | None = None
    aggregate: str | None = None  # or a rule on a total (e.g. my_cost)
    operator: Literal["eq", "neq", "lt", "lte", "gt", "gte", "in", "not_in", "between"]
    value: Any
    kind: Literal["hard", "soft"] = "hard"
    importance: int | None = Field(default=None, ge=1, le=5)  # soft rules only; asked later
    tolerance: dict | None = None                              # soft partial credit: {"limit": …}
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

    def binding(self, owner: str | None = None, category: str | None = None) -> list[Rule]:
        """Hard, active rules (optionally one owner's, on one category's candidates)."""
        return [r for r in self.rules if r.kind == "hard" and r.state == "active"
                and (owner is None or r.owner == owner) and (category is None or r.category == category)]


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
    """A value of an attribute's type, in comparable form (times as minutes)."""
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
        # Flexible: firm at the top of the range they gave; what they'd like stays a soft preference.
        limit = (row.get("authorized_range") or {}).get("max", value)
        out.append(Rule(**target, operator=spec["operator"], value=limit, **common))
        out.append(Rule(**target, operator=spec["operator"], value=value, kind="soft", **common))
    else:
        firm = row.get("hardness", "HARD") == "HARD" or spec.get("enforce_soft", False)
        out.append(Rule(**target, operator=spec["operator"], value=value, kind="hard" if firm else "soft", **common))
    for rule in out:
        validate(rule)
    return out
