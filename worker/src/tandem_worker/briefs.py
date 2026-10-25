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
    if row.get("hardness", "HARD") == "HARD":
        return int(row["value"])
    # A flexible budget is capped only at a top the owner stated; "around $800" alone is a
    # preference, weighed by the optimizer, never a cap.
    allowed = row.get("authorized_range") if isinstance(row.get("authorized_range"), dict) else {}
    return int(allowed["max"]) if "max" in allowed else None


def brief_from_constraints(owner: str, rows: dict[str, dict], preferences: Preferences, reminders_opted_in=False) -> Brief:
    """`rows` maps constraint key → {value, hardness, authorized_range?, private_rationale?}."""
    flight, trip = _cap(rows.get("flight_budget_total")), _cap(rows.get("trip_budget_total"))
    budget = rows.get("flight_budget_total") or rows.get("trip_budget_total") or {}
    value = lambda key: (rows.get(key) or {}).get("value")
    # Limits the planner enforces come only from what was said firmly; preferences reach
    # the optimizer as soft rules instead (so "prefer nonstop" never filters a search).
    firm = lambda key: value(key) if (rows.get(key) or {}).get("hardness", "HARD") == "HARD" else None
    note = value("trip_note")
    return Brief(owner=owner, hard_cap_cents=flight if flight is not None else NO_CONFIRMED_CAP, trip_cap_cents=trip,
        preferences=preferences, private_reason=budget.get("private_rationale") or "",
        baggage_required=bool(firm("baggage_required")), reminders_opted_in=reminders_opted_in,
        own_bedroom=bool(firm("own_bedroom")), stay_min_rating=firm("stay_min_rating"),
        origin_airport=value("origin_airport"), arrival_window=firm("arrival_window"), max_stops=firm("max_stops"),
        no_budget_limit=bool(value("no_budget_limit")), notes=(note,) if isinstance(note, str) else (),
        answered=tuple(sorted(rows)))


def rules_from_brief(brief: Brief):
    """A Brief's limits as rules, through the same old-key conversion the database
    mirrors confirmed limits with (for backends that hold Briefs, like the local
    test harness)."""
    from .rules import from_legacy
    rows = {"flight_budget_total": None if brief.hard_cap_cents >= NO_CONFIRMED_CAP else brief.hard_cap_cents,
            "trip_budget_total": brief.trip_cap_cents, "baggage_required": brief.baggage_required or None,
            "arrival_window": brief.arrival_window, "max_stops": brief.max_stops, "own_bedroom": brief.own_bedroom or None,
            "stay_min_rating": brief.stay_min_rating, "origin_airport": brief.origin_airport}
    out = []
    for key, value in rows.items():
        if value is not None:
            out += from_legacy(brief.owner, key, {"value": value, "hardness": "HARD",
                                                  "private_rationale": brief.private_reason})
    return out


FIELDS = {"own_bedroom": bool, "baggage_required": bool, "no_budget_limit": bool, "stay_min_rating": None,
          "origin_airport": None, "arrival_window": None, "max_stops": None}


def apply_row(brief: Brief, key: str, row: dict) -> Brief:
    """`brief` with one newly confirmed constraint row applied (other fields untouched)."""
    update: dict = {"answered": tuple(sorted({*brief.answered, key}))}
    if key == "flight_budget_total":
        cap = _cap(row)
        update.update(hard_cap_cents=NO_CONFIRMED_CAP if cap is None else cap,
                      private_reason=row.get("private_rationale") or brief.private_reason)
    elif key == "trip_budget_total":
        update.update(trip_cap_cents=_cap(row), private_reason=row.get("private_rationale") or brief.private_reason)
    elif key == "trip_note" and isinstance(row.get("value"), str):
        update["notes"] = (*brief.notes, row["value"])
    elif key in FIELDS:
        # Only a firm statement becomes a limit (the airport is a fact, not a limit).
        said = row.get("value") if key == "origin_airport" or row.get("hardness", "HARD") == "HARD" else None
        update[key] = FIELDS[key](said) if FIELDS[key] else said
    return Brief.model_validate({**brief.model_dump(), **update})
