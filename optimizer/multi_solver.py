"""Bounded multi-selection for Tandem: one item per group, generic aggregates.

Preserves optimizer v1 satisfaction/history/cost objective and does not mutate state.
Unsupported selections fail closed. Monetary amounts must share a comparable unit.
"""
from __future__ import annotations
from itertools import product
from math import isfinite, prod
from typing import Any, Mapping
from .constraint_evaluator import ConstraintValidationError, evaluate_candidate
from .scoring import score_candidate
from .solver import ObjectiveWeights, SCALE

MAX_COMBINATIONS = 10000


def _err(message):
    raise ConstraintValidationError(message)


def _validate(req, cost_attribute):
    fields = ('schema_version', 'session_id', 'task_id', 'task_type', 'revision',
              'participants', 'candidates', 'constraints', 'compromise_history',
              'locked_candidate_ids', 'max_results', 'selection_groups', 'aggregate_rules')
    if not isinstance(req, Mapping):
        _err('request must be an object')
    for field in fields:
        if field not in req:
            _err(f'missing field {field}')
    if req['schema_version'] != '1.0' or req['task_type'] != 'multi_selection':
        _err('expected schema_version 1.0 and multi_selection')
    if any(not isinstance(req[k], str) or not req[k] for k in ('session_id', 'task_id')):
        _err('session_id and task_id must be nonempty strings')
    if type(req['revision']) is not int or req['revision'] < 0:
        _err('revision must be nonnegative integer')
    if type(req['max_results']) is not int or req['max_results'] < 1:
        _err('max_results must be positive integer')
    for k in ('participants', 'candidates', 'constraints', 'locked_candidate_ids',
              'selection_groups', 'aggregate_rules'):
        if not isinstance(req[k], (list, tuple)):
            _err(f'{k} must be an array')
    if not isinstance(req['compromise_history'], Mapping):
        _err('compromise_history must be an object')
    if not isinstance(cost_attribute, str) or not cost_attribute:
        _err('cost_attribute must be a nonempty string')
    pids = [p.get('id') for p in req['participants']]
    if not pids or any(not isinstance(p, str) or not p for p in pids) or len(pids) != len(set(pids)):
        _err('participant ids must be unique strings')
    if set(req['compromise_history']) - set(pids):
        _err('unknown participant in history')
    for h in req['compromise_history'].values():
        if type(h) not in (float, int) or not isfinite(h) or h < 0:
            _err('history must have nonnegative finite values')
    by_id = {}
    for c in req['candidates']:
        if not isinstance(c, Mapping) or not isinstance(c.get('id'), str) or not c['id']:
            _err('candidate requires id')
        if c['id'] in by_id or not isinstance(c.get('attributes'), Mapping):
            _err('duplicate id or missing attributes')
        by_id[c['id']] = c
    groups, used, gids = req['selection_groups'], set(), set()
    if not groups:
        _err('selection_groups may not be empty')
    for g in groups:
        if not isinstance(g, Mapping) or not isinstance(g.get('id'), str) or not g['id'] or '.' in g['id'] or g['id'] in gids:
            _err('invalid or duplicate selection group id')
        gids.add(g['id'])
        if type(g.get('required_count')) is not int or g['required_count'] != 1:
            _err('v1 supports required_count=1 only')
        ids = g.get('candidate_ids')
        if not isinstance(ids, (list, tuple)) or not ids or len(set(ids)) != len(ids):
            _err('each selection group requires distinct candidate_ids')
        for cid in ids:
            if cid not in by_id or cid in used:
                _err('every candidate must belong to one group')
            used.add(cid)
    if used != set(by_id):
        _err('every candidate must belong to one group')
    locks = req['locked_candidate_ids']
    if len(set(locks)) != len(locks) or any(cid not in by_id for cid in locks):
        _err('invalid locks')
    for g in groups:
        if len(set(locks) & set(g['candidate_ids'])) > 1:
            _err('multiple locks in one group')
    cids = set()
    for c in req['constraints']:
        if not isinstance(c, Mapping) or not isinstance(c.get('id'), str) or c['id'] in cids:
            _err('invalid or duplicate constraint')
        cids.add(c['id'])
        if 'selection_group_id' in c and c['selection_group_id'] not in gids:
            _err('unknown selection_group_id')
    names = {'total_cost'}
    for rule in req['aggregate_rules']:
        if not isinstance(rule, Mapping) or not isinstance(rule.get('attribute'), str) or not rule['attribute'] or rule['attribute'] in names:
            _err('invalid aggregate attribute')
        names.add(rule['attribute'])
        if rule.get('operation') not in ('sum', 'spread', 'max', 'min'):
            _err('unsupported aggregate operation')
        if not isinstance(rule.get('source_attribute'), str) or not rule['source_attribute']:
            _err('aggregate requires source_attribute')
        rg = rule.get('selection_group_ids')
        if not isinstance(rg, (list, tuple)) or not rg or len(set(rg)) != len(rg) or any(x not in gids for x in rg):
            _err('invalid aggregate selection_group_ids')
    for c in req['constraints']:
        if 'selection_group_id' not in c and c.get('attribute') not in names:
            _err('unscoped constraint must target an aggregate')
    return groups, by_id


def _combination(selection, groups, req, cost_attribute):
    chosen = dict(zip([g['id'] for g in groups], selection))
    attributes = {}
    total_cost = 0.0
    for gid, candidate in chosen.items():
        for key, value in candidate['attributes'].items():
            attributes[f'{gid}.{key}'] = value
        cost = candidate['attributes'].get(cost_attribute)
        if type(cost) not in (int, float) or not isfinite(cost) or cost < 0:
            _err(f"{candidate['id']}: missing or invalid {cost_attribute}")
        total_cost += float(cost)
    attributes['total_cost'] = total_cost
    for rule in req['aggregate_rules']:
        values = [chosen[gid]['attributes'].get(rule['source_attribute']) for gid in rule['selection_group_ids']]
        if any(v is None for v in values):
            attributes[rule['attribute']] = None
            continue
        if any(type(v) not in (int, float) or not isfinite(v) for v in values):
            _err('aggregate requires finite numeric values')
        op = rule['operation']
        attributes[rule['attribute']] = (max(values) - min(values) if op == 'spread' else
                                         sum(values) if op == 'sum' else
                                         max(values) if op == 'max' else min(values))
    constraints = []
    for item in req['constraints']:
        c = dict(item)
        if 'selection_group_id' in c:
            c['attribute'] = c.pop('selection_group_id') + '.' + c['attribute']
        constraints.append(c)
    return {'id': '|'.join(c['id'] for c in selection), 'attributes': attributes}, constraints, total_cost


def optimize_multi(request: Mapping[str, Any], *, cost_attribute='price_cents',
                   weights: ObjectiveWeights = ObjectiveWeights(),
                   max_combinations=MAX_COMBINATIONS) -> dict[str, Any]:
    """Exhaustively rank small compatible bundles using the existing scoring system."""
    groups, by_id = _validate(request, cost_attribute)
    if type(max_combinations) is not int or max_combinations < 1:
        _err('max_combinations must be a positive integer')
    locks = set(request['locked_candidate_ids'])
    options = []
    for g in groups:
        selected = [by_id[cid] for cid in g['candidate_ids'] if cid in locks]
        options.append(selected or [by_id[cid] for cid in g['candidate_ids']])
    count = prod(len(x) for x in options)
    if count > max_combinations:
        _err(f'{count} combinations exceed max_combinations={max_combinations}')
    result = dict(schema_version='1.0', session_id=request['session_id'],
                  task_id=request['task_id'], revision=request['revision'],
                  status='INFEASIBLE', solver_status='NOT_RUN', proposals=[],
                  conflicts=[], suggested_relaxations=[], combinations_examined=count)
    feasible, incomplete, failures = [], [], []
    for selection in product(*options):
        bundle, constraints, cost = _combination(selection, groups, request, cost_attribute)
        evaluation = evaluate_candidate(bundle, constraints)
        ids = [c['id'] for c in selection]
        if evaluation.hard_feasibility != 'FEASIBLE':
            failures.append({'candidate_ids': ids,
                             'violated_hard_constraints': evaluation.violated_hard_constraints,
                             'unknown_hard_constraints': evaluation.unknown_hard_constraints})
            continue
        score = score_candidate(bundle, constraints, request['participants'], request['compromise_history'])
        if not score.all_soft_preferences_known:
            incomplete.append({'candidate_ids': ids, 'unknown_soft_preferences': [
                p.constraint_id for p in score.preferences if p.satisfaction is None]})
            continue
        feasible.append((ids, score, cost, evaluation.violated_soft_preferences))
    if not feasible:
        result['status'] = ('NEEDS_DATA' if incomplete or any(f['unknown_hard_constraints'] for f in failures)
                            else 'NEEDS_REVIEW' if locks else 'INFEASIBLE')
        result['conflicts'] = (failures + incomplete)[:50]
        return result
    lo, hi = min(x[2] for x in feasible), max(x[2] for x in feasible)
    ranking = []
    for ids, score, cost, soft in feasible:
        normalized = (cost - lo) / (hi - lo) if hi > lo else 0.0
        objective = (weights.alpha * score.max_projected_burden +
                     weights.beta * score.total_new_compromise_burden +
                     weights.gamma * normalized)
        ranking.append((round(objective * SCALE), tuple(ids), objective, ids, score, cost, normalized, soft))
    ranking.sort(key=lambda r: (r[0], r[1]))
    result['status'], result['solver_status'] = 'FEASIBLE', 'OPTIMAL'
    for index, (_, _, obj, ids, score, cost, normalized, soft) in enumerate(ranking[:request['max_results']], 1):
        result['proposals'].append({
            'rank': index, 'candidate_ids': ids,
            'scores': {'group_satisfaction': score.group_satisfaction, 'fairness': score.fairness,
                       'normalized_cost': normalized, 'total_cost': cost,
                       'max_projected_burden': score.max_projected_burden,
                       'total_new_compromise_burden': score.total_new_compromise_burden,
                       'objective_value': obj},
            'per_person': [p.to_dict() for p in score.per_person],
            'violated_soft_preferences': soft, 'violated_hard_constraints': [],
            'requires_approval': True})
    if incomplete:
        result['ranking_scope'] = 'COMPLETE_COMBINATIONS_ONLY'
        result['excluded_unknown_combinations'] = incomplete[:25]
    return result
