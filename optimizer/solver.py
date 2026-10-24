"""Tandem optimizer v1: rank feasible single-choice proposals fairly.

Input contract: the existing schema_version=1.0 request shared with teammates.
This module is deliberately domain-agnostic. No database writes, booking, agent
calls, constraint relaxation, or automatic approvals occur here.

For one-of-N choices, direct exhaustive ranking is equivalent to using CP-SAT.
CP-SAT can be requested explicitly with backend='cp_sat'; it is optional here
because the simplest optimization problems need no integer solver.

Important assumptions:
- monetary amounts are already comparable and use the same unit/currency;
- missing hard attributes and incomplete soft attributes are NOT treated as OK;
- score weights (alpha,beta,gamma) are experimental, not calibrated;
- locked_candidate_ids means the existing approved choice MUST be retained;
- history is never updated by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from .constraint_evaluator import ConstraintValidationError, evaluate_candidates
from .scoring import CandidateScore, score_candidates

SCALE = 10_000


@dataclass(frozen=True)
class ObjectiveWeights:
    alpha: int = 5  # worst historical+new burden
    beta: int = 3   # total new burden
    gamma: int = 1  # normalized candidate cost

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in (self.alpha, self.beta, self.gamma)):
            raise ValueError('Objective weights must be nonnegative integers')
        if self.alpha + self.beta + self.gamma == 0:
            raise ValueError('At least one objective weight must be positive')


def _validate_request(request: Mapping[str, Any]) -> None:
    for key in ('schema_version', 'session_id', 'task_id', 'revision', 'task_type',
                'participants', 'candidates', 'constraints', 'compromise_history',
                'locked_candidate_ids', 'max_results'):
        if key not in request:
            raise ConstraintValidationError(f'Missing request field: {key}')
    if request['schema_version'] != '1.0' or request['task_type'] != 'single_selection':
        raise ConstraintValidationError('v1 solver supports schema_version 1.0 and single_selection only')
    if type(request['revision']) is not int or request['revision'] < 0:
        raise ConstraintValidationError('revision must be a nonnegative integer')
    if any(not isinstance(request[key], str) or not request[key] for key in ('session_id', 'task_id')):
        raise ConstraintValidationError('session_id and task_id must be nonempty strings')
    if type(request['max_results']) is not int or request['max_results'] < 1:
        raise ConstraintValidationError('max_results must be a positive integer')
    if not isinstance(request['compromise_history'], Mapping):
        raise ConstraintValidationError('compromise_history must be an object')
    for key in ('participants', 'candidates', 'constraints', 'locked_candidate_ids'):
        if not isinstance(request[key], (list, tuple)):
            raise ConstraintValidationError(f'{key} must be an array')
    locks = request['locked_candidate_ids']
    if len(locks) > 1:
        raise ConstraintValidationError('single_selection supports at most one locked candidate')
    ids = [candidate.get('id') for candidate in request['candidates']]
    if len(ids) != len(set(ids)):
        raise ConstraintValidationError('Duplicate candidate IDs')
    if any(lock not in ids for lock in locks):
        raise ConstraintValidationError('Locked candidate must exist in candidates')


def _costs(candidates: list[Mapping[str, Any]], cost_attribute: str | None) -> dict[str, float]:
    """Min-max normalized costs; no cost metric -> all zero.

    Never infer a zero cost for a candidate with missing cost data when this
    attribute is enabled. The caller must enrich candidates beforehand.
    """
    if cost_attribute is None:
        return {c['id']: 0.0 for c in candidates}
    raw = {}
    for c in candidates:
        value = c['attributes'].get(cost_attribute)
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ConstraintValidationError(
                f"{c['id']}: comparable nonnegative numeric cost '{cost_attribute}' required"
            )
        raw[c['id']] = float(value)
    if not raw:
        return {}
    lo, hi = min(raw.values()), max(raw.values())
    return {key: ((value - lo) / (hi - lo) if hi > lo else 0.0)
            for key, value in raw.items()}


def _make_proposal(score: CandidateScore, rank: int, cost: float, objective: float,
                   violated_soft: list[str]) -> dict[str, Any]:
    return {
        'rank': rank,
        'candidate_ids': [score.candidate_id],
        'scores': {
            'group_satisfaction': score.group_satisfaction,
            'fairness': score.fairness,
            'normalized_cost': cost,
            'max_projected_burden': score.max_projected_burden,
            'total_new_compromise_burden': score.total_new_compromise_burden,
            'objective_value': objective,
        },
        'per_person': [person.to_dict() for person in score.per_person],
        'violated_soft_preferences': violated_soft,
        'violated_hard_constraints': [],
        'requires_approval': True,
    }


def _cp_sat_order(ids: list[str], scaled: dict[str, int], max_results: int,
                  time_limit_seconds: float) -> tuple[list[str], str]:
    """Rank up to K options via repeated CP-SAT solves. Import is optional."""
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise RuntimeError('Install OR-Tools first: python3 -m pip install ortools') from exc
    ordered = []
    remaining = list(ids)
    all_optimal = True
    for _ in range(min(max_results, len(ids))):
        model = cp_model.CpModel()
        choices = {cid: model.new_bool_var(f'choose_{i}') for i, cid in enumerate(remaining)}
        model.add(sum(choices.values()) == 1)
        # Large magnitudes can overflow int64; validate before calling.
        model.minimize(sum(scaled[cid] * choices[cid] for cid in remaining))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_search_workers = 1
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not ordered:
                return [], ('UNKNOWN' if status == cp_model.UNKNOWN else
                            'MODEL_INVALID' if status == cp_model.MODEL_INVALID else 'INFEASIBLE')
            break
        if status != cp_model.OPTIMAL:
            all_optimal = False
        selected = next(cid for cid in remaining if solver.value(choices[cid]))
        ordered.append(selected)
        remaining.remove(selected)
    return ordered, 'OPTIMAL' if all_optimal and len(ordered) == min(max_results, len(ids)) else 'FEASIBLE'


def optimize(
    request: Mapping[str, Any],
    *,
    backend: str = 'direct',
    cost_attribute: str | None = 'price_per_person',
    weights: ObjectiveWeights = ObjectiveWeights(),
    time_limit_seconds: float = 3.0,
) -> dict[str, Any]:
    """Return schema-v1 proposals without committing them.

    Pass cost_attribute=None for non-priced decisions; otherwise the named
    attribute must exist on all eligible complete candidates. For now the
    schema only supports exactly one chosen candidate.
    """
    _validate_request(request)
    if backend not in ('direct', 'cp_sat'):
        raise ValueError("backend must be 'direct' or 'cp_sat'")
    if not isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise ValueError('time_limit_seconds must be positive and finite')
    evaluated = evaluate_candidates(request['candidates'], request['constraints'])
    scores = score_candidates(request['candidates'], request['constraints'],
                              request['participants'], request['compromise_history'])
    score_map = {s.candidate_id: s for s in scores}
    eval_map = {e.candidate_id: e for e in evaluated}
    base = {
        'schema_version': request['schema_version'],
        'session_id': request['session_id'],
        'task_id': request['task_id'],
        'revision': request['revision'],
        'proposals': [],
        'conflicts': [],
        'suggested_relaxations': [],
    }
    eligible = [c for c in request['candidates'] if score_map[c['id']].hard_feasibility == 'FEASIBLE']
    if not eligible:
        any_unknown = any(e.hard_feasibility == 'UNKNOWN' for e in evaluated)
        base['status'] = 'NEEDS_DATA' if any_unknown else 'INFEASIBLE'
        base['solver_status'] = 'NOT_RUN'
        base['conflicts'] = [
            {'candidate_id': e.candidate_id,
             'violated_hard_constraints': e.violated_hard_constraints,
             'unknown_hard_constraints': e.unknown_hard_constraints}
            for e in evaluated
        ]
        return base
    complete = [c for c in eligible if score_map[c['id']].all_soft_preferences_known]
    if not complete:
        base['status'] = 'NEEDS_DATA'
        base['solver_status'] = 'NOT_RUN'
        base['conflicts'] = [
            {'candidate_id': c['id'], 'unknown_soft_preferences': [
                p.constraint_id for p in score_map[c['id']].preferences if p.satisfaction is None
            ]} for c in eligible
        ]
        return base
    # Do not silently discount unknown soft preferences for some candidates.
    # Exclude incomplete options until their data is enriched.
    locks = request['locked_candidate_ids']
    if locks:
        if locks[0] not in {c['id'] for c in complete}:
            base['status'] = 'NEEDS_REVIEW'
            base['solver_status'] = 'NOT_RUN'
            base['conflicts'] = [{'candidate_id': locks[0],
                                  'reason': 'Locked option is infeasible or missing required attributes'}]
            return base
        complete = [c for c in complete if c['id'] == locks[0]]
    costs = _costs(complete, cost_attribute)
    objective = {}
    scaled = {}
    for c in complete:
        score = score_map[c['id']]
        val = (weights.alpha * score.max_projected_burden
               + weights.beta * score.total_new_compromise_burden
               + weights.gamma * costs[c['id']])
        objective[c['id']] = val
        scaled_val = round(val * SCALE)
        if not 0 <= scaled_val < 2**62:
            raise ConstraintValidationError('Objective is too large for a safe integer solver coefficient')
        scaled[c['id']] = scaled_val
    if backend == 'direct':
        ordered = sorted(objective, key=lambda cid: (scaled[cid], cid))[:request['max_results']]
        solver_status = 'OPTIMAL'
    else:
        ordered, solver_status = _cp_sat_order(
            sorted(objective), scaled, request['max_results'], time_limit_seconds)
    if not ordered:
        base['status'] = 'SOLVER_ERROR' if solver_status == 'MODEL_INVALID' else 'UNRESOLVED'
        base['solver_status'] = solver_status
        return base
    base['status'] = 'FEASIBLE'
    base['solver_status'] = solver_status
    base['proposals'] = [
        _make_proposal(score_map[cid], idx, costs[cid], objective[cid],
                       eval_map[cid].violated_soft_preferences)
        for idx, cid in enumerate(ordered, 1)
    ]
    # If incomplete but hard-feasible options were omitted, ranking is partial.
    if len(complete) < len(eligible):
        base['ranking_scope'] = 'COMPLETE_CANDIDATES_ONLY'
        base['excluded_unknown_candidate_ids'] = [
            c['id'] for c in eligible if not score_map[c['id']].all_soft_preferences_known
        ]
    return base
