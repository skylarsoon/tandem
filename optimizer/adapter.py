"""SpacetimeDB execution-context -> Tandem optimizer v1 request adapter.

Pure boundary translator. No DB writes, approval, or negotiation.

Currently supports ONE selection from a single candidate category. For FLIGHT,
provide owner_member_id: group flight bundles and trip_budget_total are not yet
supported by the optimizer's single_selection model.

The adapter fails closed on unknown active constraint keys, missing costs,
unconfirmed soft importance, stale revisions, and malformed facts. It deliberately
never copies private_rationale into the optimizer request.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


class AdapterError(ValueError):
    """A backend contract cannot be safely represented as an optimizer-v1 request."""


@dataclass(frozen=True)
class AdaptedOptimization:
    request: dict[str, Any]
    # Only the adapter keeps mapping metadata; solver input stays schema v1.
    candidate_ref_by_id: dict[str, str]


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdapterError(f'{field} must be an object')
    return value


def _facts(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    if 'facts' in candidate:
        return _object(candidate['facts'], 'candidate.facts')
    raw = candidate.get('facts_json')
    if not isinstance(raw, str):
        raise AdapterError('candidate must have facts or facts_json')
    try:
        return _object(json.loads(raw), 'candidate.facts_json')
    except (ValueError, TypeError) as exc:
        raise AdapterError('malformed facts_json') from exc


def _value(value: Any) -> Any:
    # Backend JSON values are sometimes carried as strings. A string such as
    # '50000' is parsed to 50000; ordinary textual values are retained.
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _money(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise AdapterError(f'{field} must be a nonnegative integer number of cents')
    return value


def adapt_execution_context(
    context: Mapping[str, Any],
    *,
    category: str,
    task_id: str,
    compromise_history: Mapping[str, float],
    owner_member_id: int | None = None,
    importance_by_constraint_id: Mapping[str, int] | None = None,
    extra_constraints: list[Mapping[str, Any]] | None = None,
    locked_candidate_refs: list[str] | None = None,
    max_results: int = 3,
) -> AdaptedOptimization:
    """Translate one current job snapshot to the existing schema-v1 request.

    Category is one of FLIGHT/STAY/TRANSFER/ACTIVITY/DINNER. FLIGHT requires
    owner_member_id and selects one flight *for that person*, not a group bundle.

    Backend keys explicitly supported here:
      flight_budget_total (FLIGHT, owner-scoped, hard upper bound)
      baggage_required (FLIGHT, owner-scoped, hard, only when true)
      stay_min_rating (STAY, soft or hard minimum; soft requires importance)

    Other backend constraints require a consciously designed mapping rather than
    silently being dropped. The general optimizer itself remains domain-neutral.
    """
    ctx = _object(context, 'context')
    job = _object(ctx.get('job'), 'job')
    room = _object(ctx.get('room'), 'room')
    if ctx.get('ok') is False:
        raise AdapterError('backend execution context reports failure')
    if job.get('kind') != 'PLAN' or job.get('room_id') != room.get('room_id'):
        raise AdapterError('expected a PLAN job matching room')
    if type(job.get('input_revision')) is not int or job['input_revision'] < 0:
        raise AdapterError('missing input_revision')
    if type(job.get('membership_revision')) is not int:
        raise AdapterError('missing membership_revision')
    if category not in {'FLIGHT', 'STAY', 'TRANSFER', 'ACTIVITY', 'DINNER'}:
        raise AdapterError(f'unsupported candidate category: {category}')
    if category == 'FLIGHT' and type(owner_member_id) is not int:
        raise AdapterError('FLIGHT requires owner_member_id; multi-flight bundles unsupported')
    if category != 'FLIGHT' and owner_member_id is not None:
        raise AdapterError('owner_member_id is only supported for individual FLIGHT tasks')
    if type(max_results) is not int or max_results < 1:
        raise AdapterError('max_results must be positive')
    if not isinstance(task_id, str) or not task_id:
        raise AdapterError('task_id must be nonempty')
    if not isinstance(compromise_history, Mapping):
        raise AdapterError('pass the session compromise_history explicitly; never infer/reset it')

    active = [m for m in ctx.get('members', []) if m.get('active') is True]
    if not active or any(type(m.get('member_id')) is not int for m in active):
        raise AdapterError('no valid active participants')
    ids = [str(m['member_id']) for m in active]
    if len(ids) != len(set(ids)):
        raise AdapterError('duplicate member IDs')
    if category == 'FLIGHT' and str(owner_member_id) not in ids:
        raise AdapterError('flight owner is not an active participant')
    if set(compromise_history) != set(ids):
        raise AdapterError('session compromise history must include exactly all active member IDs as strings')
    for k, v in compromise_history.items():
        if type(v) not in (int, float) or not isfinite(v) or v < 0:
            raise AdapterError(f'invalid compromise history for {k}')

    matched = []
    for c in ctx.get('candidates', []):
        if c.get('category') != category:
            continue
        facts = _facts(c)
        if category == 'FLIGHT' and facts.get('owner_member_id') != owner_member_id:
            continue
        if (c.get('input_revision') != job['input_revision'] or
                c.get('membership_revision') != job['membership_revision']):
            raise AdapterError(f"stale candidate revisions: {c.get('candidate_ref')}")
        if c.get('missing_fields'):
            raise AdapterError(f"candidate has missing fields: {c.get('candidate_ref')}")
        ref = c.get('candidate_ref')
        if not isinstance(ref, str) or not ref:
            raise AdapterError('candidate_ref must be nonempty')
        price = _money(facts.get('total_cents'), f'{ref}.total_cents')
        if facts.get('currency') != 'USD':
            raise AdapterError(f'{ref}: only normalized USD candidates supported in v1')
        # COMPLETE_* is essential for approval-ready comparisons; never turn
        # scraper-observed prices with unknown fees into complete costs.
        expected_basis = {'FLIGHT':'COMPLETE_FLIGHT_TOTAL', 'STAY':'COMPLETE_STAY_TOTAL',
                          'TRANSFER':'COMPLETE_TRANSFER_TOTAL'}
        if category in expected_basis and facts.get('cost_basis') != expected_basis[category]:
            raise AdapterError(f'{ref}: not a complete verified cost basis')
        # No per-person price split is inferred for shared items. The v1 solver
        # ranks total candidate costs; group-budget constraints need more modeling.
        safe_attributes = {k: v for k, v in facts.items()
                           if k not in {'private_rationale'} and
                           (v is None or type(v) in (str, bool, int, float))}
        safe_attributes['price_cents'] = price
        matched.append({'id': ref, 'attributes': safe_attributes})
    if len({c['id'] for c in matched}) != len(matched):
        raise AdapterError('duplicate candidate refs')

    priorities = importance_by_constraint_id or {}
    translated = []
    for c in ctx.get('constraints', []):
        key = c.get('key')
        owner = c.get('member_id')
        if str(owner) not in ids:
            raise AdapterError(f'constraint has unknown owner: {owner}')
        # Explicit category mismatches are outside the current task. Never
        # silently ignore *trip-wide* constraints, which combine booking costs.
        if key == 'trip_budget_total':
            raise AdapterError('trip_budget_total requires multi-item spending calculation')
        if key in ('flight_budget_total', 'baggage_required') and category != 'FLIGHT':
            continue
        if key == 'stay_min_rating' and category != 'STAY':
            continue
        if category == 'FLIGHT' and owner != owner_member_id:
            continue
        hardness = c.get('hardness')
        if hardness not in ('HARD', 'SOFT'):
            raise AdapterError(f'{key}: unknown hardness')
        cid = str(c.get('constraint_id'))
        raw = _value(c.get('value'))
        if key == 'flight_budget_total':
            if c.get('unit') != 'USD_cents':
                raise AdapterError('flight budget must be USD_cents')
            attr, op, expected = 'price_cents', 'lte', _money(raw, 'flight_budget_total')
        elif key == 'baggage_required':
            if type(raw) is not bool:
                raise AdapterError('baggage_required must be boolean')
            if not raw:
                continue  # False is no requirement, not a ban on checked bags.
            attr, op, expected = 'baggage_included', 'eq', True
        elif key == 'stay_min_rating':
            if type(raw) not in (int, float) or not isfinite(raw):
                raise AdapterError('stay_min_rating must be numeric')
            attr, op, expected = 'rating', 'gte', raw
        else:
            raise AdapterError(f'unsupported backend constraint key: {key!r}')
        row = {'id': cid, 'owner_id': str(owner), 'kind': hardness.lower(),
               'attribute': attr, 'operator': op, 'value': expected}
        if hardness == 'SOFT':
            importance = priorities.get(cid)
            if type(importance) is not int or not 1 <= importance <= 5:
                raise AdapterError(f'{cid}: soft importance 1-5 needs explicit mapping')
            row['importance'] = importance
        translated.append(row)

    # Extra constraints are already in the optimizer's universal schema, e.g.
    # user-confirmed venue preferences extracted upstream. Never invent weights.
    for extra in extra_constraints or []:
        row = dict(_object(extra, 'extra_constraint'))
        if row.get('owner_id') not in ids:
            raise AdapterError('extra constraint owner must be an active participant')
        translated.append(row)
    if len({c['id'] for c in translated}) != len(translated):
        raise AdapterError('duplicate normalized constraint ids')
    locks = list(locked_candidate_refs or [])
    if any(x not in {c['id'] for c in matched} for x in locks):
        raise AdapterError('locked candidate not present in current task candidates')
    req = {
        'schema_version': '1.0',
        'session_id': str(room['room_id']),
        'task_id': task_id,
        'task_type': 'single_selection',
        'revision': job['input_revision'],
        'participants': [{'id': pid} for pid in ids],
        'candidates': matched,
        'constraints': translated,
        'compromise_history': dict(compromise_history),
        'locked_candidate_ids': locks,
        'max_results': max_results,
    }
    return AdaptedOptimization(request=req,
                               candidate_ref_by_id={c['id']: c['id'] for c in matched})
