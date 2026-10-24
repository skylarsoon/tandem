"""A traveller's confirmed constraints → the Brief the planner reads. Shared by
every backend so the same rows always mean the same limits."""
from __future__ import annotations

from .contracts import Brief, Preferences

# Planning without a stated budget is only allowed in rooms built without
# onboarding; onboarding requires a budget or an explicit no_budget_limit.
NO_CONFIRMED_CAP = 10**12


def _cap(row: dict | None) -> int | None:
    if not row:
        return None
    if row.get("hardness") == "HARD":
        return int(row["value"])
    # A flexible budget can be explored up to the range the owner gave, never beyond.
    allowed = row.get("authorized_range") if isinstance(row.get("authorized_range"), dict) else {}
    return int(allowed.get("max", row["value"]))


def brief_from_constraints(owner: str, rows: dict[str, dict], preferences: Preferences, reminders_opted_in=False) -> Brief:
    """`rows` maps constraint key → {value, hardness, authorized_range?, private_rationale?}."""
    flight, trip = _cap(rows.get("flight_budget_total")), _cap(rows.get("trip_budget_total"))
    budget = rows.get("flight_budget_total") or rows.get("trip_budget_total") or {}
    value = lambda key: (rows.get(key) or {}).get("value")
    note = value("trip_note")
    return Brief(owner=owner, hard_cap_cents=flight if flight is not None else NO_CONFIRMED_CAP, trip_cap_cents=trip,
        preferences=preferences, private_reason=budget.get("private_rationale") or "",
        baggage_required=bool(value("baggage_required")), reminders_opted_in=reminders_opted_in,
        own_bedroom=bool(value("own_bedroom")), stay_min_rating=value("stay_min_rating"),
        origin_airport=value("origin_airport"), arrival_window=value("arrival_window"), max_stops=value("max_stops"),
        no_budget_limit=bool(value("no_budget_limit")), notes=(note,) if isinstance(note, str) else ())
