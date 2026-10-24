"""SpacetimeDB -> bounded, domain-neutral multi-selection request.

The first integration path groups FLIGHT candidates by active member, without
putting flight-specific rules into multi_solver.py. No DB writes or approvals.
Uses the existing adapter's input helpers. Unknown active constraint keys fail
closed rather than being silently lost.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from .adapter import AdapterError, AdaptedOptimization, _facts, _money, _object, _value


def adapt_multi_execution_context(
    context: Mapping[str, Any], *, task_id: str,
    compromise_history: Mapping[str, float],
    category: str = 'FLIGHT',
    importance_by_constraint_id: Mapping[str, int] | None = None,
    extra_constraints: list[Mapping[str, Any]] | None = None,
    locked_candidate_refs: list[str] | None = None,
    max_candidates_per_group: int = 5,
    max_results: int = 3,
    now_epoch: float | None = None,
) -> AdaptedOptimization:
    """Make a multi_selection request for one candidate per participant.

    This *boundary mapper* supports flight bundles today; it emits generic
    selection groups, aggregate rules, and constraints for multi_solver.py.
    Cross-category trip budgets/shares are NOT inferred or ignored: such inputs
    fail until an explicit allocation/previous-commitment contract exists.
    """
    ctx = _object(context, 'context')
    job = _object(ctx.get('job'), 'job')
    room = _object(ctx.get('room'), 'room')
    if ctx.get('ok') is False or job.get('kind') != 'PLAN' or job.get('room_id') != room.get('room_id'):
        raise AdapterError('expected a successful PLAN context with matching room')
    if any(type(job.get(k)) is not int or job[k] < 0 for k in ('input_revision', 'membership_revision')):
        raise AdapterError('missing job revisions')
    if not isinstance(task_id, str) or not task_id:
        raise AdapterError('task_id must be nonempty string')
    if category != 'FLIGHT':
        raise AdapterError('initial multi adapter supports FLIGHT grouping only; solver is domain-agnostic')
    if type(max_candidates_per_group) is not int or not 1 <= max_candidates_per_group <= 5:
        raise AdapterError('max_candidates_per_group must be 1..5')
    if type(max_results) is not int or max_results < 1:
        raise AdapterError('max_results must be positive')
    if now_epoch is not None and (type(now_epoch) not in (int, float) or not isfinite(now_epoch)):
        raise AdapterError('now_epoch must be finite')
    members = [m for m in ctx.get('members', []) if m.get('active') is True]
    mids = [m.get('member_id') for m in members]
    if not mids or any(type(mid) is not int for mid in mids) or len(set(mids)) != len(mids):
        raise AdapterError('active members require unique integer IDs')
    ids = [str(mid) for mid in mids]
    if not isinstance(compromise_history, Mapping) or set(compromise_history) != set(ids):
        raise AdapterError('compromise_history must include exactly all active members')
    if any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in compromise_history.values()):
        raise AdapterError('compromise_history must be finite and nonnegative')

    by_owner: dict[int, list[str]] = {mid: [] for mid in mids}
    normalized = []
    seen = set()
    for candidate in ctx.get('candidates', []):
        if candidate.get('category') != category:
            continue
        ref = candidate.get('candidate_ref')
        if not isinstance(ref, str) or not ref or ref in seen:
            raise AdapterError('duplicate or invalid candidate_ref')
        seen.add(ref)
        if (candidate.get('input_revision') != job['input_revision'] or
                candidate.get('membership_revision') != job['membership_revision']):
            raise AdapterError(f'{ref}: stale candidate revisions')
        facts = _facts(candidate)
        owner = facts.get('owner_member_id')
        if type(owner) is not int or owner not in by_owner:
            raise AdapterError(f'{ref}: owner_member_id must be an active member')
        if candidate.get('missing_fields') or facts.get('missing_critical_fields'):
            raise AdapterError(f'{ref}: missing critical fields')
        if facts.get('currency') != 'USD' or facts.get('cost_basis') != 'COMPLETE_FLIGHT_TOTAL':
            raise AdapterError(f'{ref}: requires complete comparable USD flight price')
        price = _money(facts.get('total_cents'), f'{ref}.total_cents')
        arrival = facts.get('arrival_epoch')
        if type(arrival) not in (int, float) or not isfinite(arrival):
            raise AdapterError(f'{ref}: missing numeric arrival_epoch')
        if now_epoch is not None:
            observed, expires = facts.get('observed_at'), facts.get('valid_until')
            if any(type(v) not in (int, float) or not isfinite(v) for v in (observed, expires)):
                raise AdapterError(f'{ref}: missing evidence freshness timestamps')
            if observed > now_epoch or expires <= now_epoch:
                raise AdapterError(f'{ref}: stale or future-dated evidence')
        safe = {k: v for k, v in facts.items() if k != 'private_rationale' and
                (v is None or type(v) in (str, bool, int, float))}
        safe['price_cents'] = price
        normalized.append({'id': ref, 'attributes': safe})
        by_owner[owner].append(ref)

    for mid, options in by_owner.items():
        if not options:
            raise AdapterError(f'member {mid}: no flight candidates; request more research')
        if len(options) > max_candidates_per_group:
            raise AdapterError(f'member {mid}: {len(options)} candidates exceed shortlist limit {max_candidates_per_group}')

    constraints = []
    priorities = importance_by_constraint_id or {}
    for c in ctx.get('constraints', []):
        owner = c.get('member_id')
        if owner not in by_owner:
            raise AdapterError('constraint owner is not active')
        key, hardness = c.get('key'), c.get('hardness')
        if key in ('stay_min_rating', 'own_bedroom'):
            continue  # unrelated to the FLIGHT decision; enforced when booking a stay
        if key == 'trip_budget_total':
            raise AdapterError('trip_budget_total requires committed shared spending/allocation; not silently ignored')
        if hardness not in ('HARD', 'SOFT'):
            raise AdapterError(f'{key}: invalid hardness')
        raw = _value(c.get('value'))
        if key == 'flight_budget_total':
            if c.get('unit') != 'USD_cents':
                raise AdapterError('flight_budget_total must be USD_cents')
            attribute, op, value = 'price_cents', 'lte', _money(raw, key)
        elif key == 'baggage_required':
            if type(raw) is not bool:
                raise AdapterError('baggage_required must be boolean')
            if not raw:
                continue
            attribute, op, value = 'baggage_included', 'eq', True
        else:
            raise AdapterError(f'unsupported backend constraint: {key!r}')
        cid = str(c.get('constraint_id'))
        item = {'id': cid, 'owner_id': str(owner), 'kind': hardness.lower(),
                'attribute': attribute, 'operator': op, 'value': value,
                'selection_group_id': str(owner)}
        if hardness == 'SOFT':
            importance = priorities.get(cid)
            if type(importance) is not int or not 1 <= importance <= 5:
                raise AdapterError(f'{cid}: soft importance must be explicitly mapped 1..5')
            item['importance'] = importance
        constraints.append(item)

    for extra in extra_constraints or []:
        item = dict(_object(extra, 'extra_constraint'))
        if item.get('owner_id') not in ids:
            raise AdapterError('extra_constraint owner must be an active member')
        constraints.append(item)
    if len({c.get('id') for c in constraints}) != len(constraints):
        raise AdapterError('duplicate normalized constraint ids')

    room_settings = room.get('settings') or {}
    room_settings = _object(room_settings, 'room.settings')
    overlap = room_settings.get('arrival_overlap_seconds', 7200)
    if type(overlap) is not int or overlap < 0:
        raise AdapterError('arrival_overlap_seconds must be a nonnegative integer')
    constraints.append({'id': '__arrival_window', 'owner_id': None, 'kind': 'hard',
                        'attribute': 'arrival_spread_seconds', 'operator': 'lte', 'value': overlap})
    if len({c['id'] for c in constraints}) != len(constraints):
        raise AdapterError('aggregate constraint id collision')

    locks = list(locked_candidate_refs or [])
    if len(set(locks)) != len(locks) or any(ref not in seen for ref in locks):
        raise AdapterError('locks must be distinct current candidates')
    groups = [{'id': str(mid), 'candidate_ids': by_owner[mid], 'required_count': 1} for mid in mids]
    req = {'schema_version': '1.0', 'session_id': str(room['room_id']),
           'task_id': task_id, 'task_type': 'multi_selection',
           'revision': job['input_revision'],
           'participants': [{'id': uid} for uid in ids],
           'candidates': normalized, 'constraints': constraints,
           'compromise_history': dict(compromise_history),
           'locked_candidate_ids': locks, 'max_results': max_results,
           'selection_groups': groups,
           'aggregate_rules': [{'attribute': 'arrival_spread_seconds', 'operation': 'spread',
                                'source_attribute': 'arrival_epoch', 'selection_group_ids': ids}]}
    return AdaptedOptimization(request=req, candidate_ref_by_id={ref: ref for ref in seen})
