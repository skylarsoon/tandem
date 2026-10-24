"""Domain-agnostic candidate constraint evaluation for optimizer schema v1.

This module deliberately does NOT calculate fairness, optimize, or mutate constraints.
It takes a candidate and constraint definitions and returns explicit evaluations.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite
from typing import Any, Mapping, Sequence


class ConstraintValidationError(ValueError):
    """Malformed/unsupported constraint or attribute, not candidate infeasibility."""


@dataclass(frozen=True)
class ConstraintEvaluation:
    constraint_id: str
    owner_id: str | None
    kind: str
    attribute: str
    result: str  # SATISFIED | VIOLATED | UNKNOWN
    actual_value: Any
    expected_value: Any
    operator: str
    importance: int | None = None
    tolerance_limit: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate_id: str
    hard_feasibility: str  # FEASIBLE | INFEASIBLE | UNKNOWN
    evaluations: tuple[ConstraintEvaluation, ...]

    @property
    def violated_hard_constraints(self) -> list[str]:
        return [e.constraint_id for e in self.evaluations if e.kind == 'hard' and e.result == 'VIOLATED']

    @property
    def unknown_hard_constraints(self) -> list[str]:
        return [e.constraint_id for e in self.evaluations if e.kind == 'hard' and e.result == 'UNKNOWN']

    @property
    def violated_soft_preferences(self) -> list[str]:
        return [e.constraint_id for e in self.evaluations if e.kind == 'soft' and e.result == 'VIOLATED']

    def to_dict(self) -> dict[str, Any]:
        return {
            'candidate_id': self.candidate_id,
            'hard_feasibility': self.hard_feasibility,
            'violated_hard_constraints': self.violated_hard_constraints,
            'unknown_hard_constraints': self.unknown_hard_constraints,
            'violated_soft_preferences': self.violated_soft_preferences,
            'evaluations': [e.to_dict() for e in self.evaluations],
        }

#data validation
def _validate_constraint(c: Mapping[str, Any]) -> None:
    for field in ('id', 'kind', 'attribute', 'operator', 'value'):
        if field not in c:
            raise ConstraintValidationError(f"Constraint missing required field: {field}")
    if not isinstance(c['id'], str) or not c['id']:
        raise ConstraintValidationError('Constraint id must be a nonempty string')
    if not isinstance(c['attribute'], str) or not c['attribute']:
        raise ConstraintValidationError(f"{c['id']}: attribute must be a nonempty string")
    if c['kind'] not in ('hard', 'soft'):
        raise ConstraintValidationError(f"{c['id']}: kind must be hard or soft")
    if c['operator'] not in ('eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in'):
        raise ConstraintValidationError(f"{c['id']}: unsupported operator {c['operator']!r}")
    if c['kind'] == 'soft':
        weight = c.get('importance')
        if type(weight) is not int or not 1 <= weight <= 5:
            raise ConstraintValidationError(f"{c['id']}: soft importance must be an integer 1 through 5")
    elif 'importance' in c and c['importance'] is not None:
        raise ConstraintValidationError(f"{c['id']}: hard constraints must not have importance")
    tolerance = c.get('tolerance')
    if tolerance is not None:
        if c['kind'] != 'soft' or c['operator'] not in ('lt', 'lte', 'gt', 'gte'):
            raise ConstraintValidationError(f"{c['id']}: tolerance supported only for numeric soft comparisons")
        if not isinstance(tolerance, Mapping) or 'limit' not in tolerance:
            raise ConstraintValidationError(f"{c['id']}: tolerance requires a limit")
        preferred, limit = c['value'], tolerance['limit']
        if any(type(x) not in (int, float) or not isfinite(x) for x in (preferred, limit)):
            raise ConstraintValidationError(f"{c['id']}: tolerance values must be finite numbers")
        if c['operator'] in ('lt', 'lte') and limit <= preferred:
            raise ConstraintValidationError(f"{c['id']}: upper tolerance limit must exceed preference")
        if c['operator'] in ('gt', 'gte') and limit >= preferred:
            raise ConstraintValidationError(f"{c['id']}: lower tolerance limit must be below preference")
    if c['operator'] in ('in', 'not_in') and (not isinstance(c['value'], (list, tuple, set, frozenset)) or isinstance(c['value'], (str, bytes))):
        raise ConstraintValidationError(f"{c['id']}: in/not_in requires a collection value")

#boolean or error raised
def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op in ('lt', 'lte', 'gt', 'gte'):
        if any(type(v) not in (int, float) or not isfinite(v) for v in (actual, expected)):
            raise ConstraintValidationError('Numeric comparisons require finite numbers of compatible units')
        return {'lt': lambda: actual < expected, 'lte': lambda: actual <= expected,
                'gt': lambda: actual > expected, 'gte': lambda: actual >= expected}[op]()
    if op in ('eq', 'neq'):
        if type(actual) is not type(expected) and not (type(actual) in (int, float) and type(expected) in (int, float)):
            raise ConstraintValidationError('Equality comparisons require compatible types')
        return (actual == expected) if op == 'eq' else (actual != expected)
    return actual in expected if op == 'in' else actual not in expected

#evaluating ONE candidate

#FEASIBLE: Every hard constraint is satisfied. The candidate can proceed to scoring, even if some soft preferences aren't met.
#INFEASIBLE: At least one hard constraint is violated. The candidate must not be selected.
#UNKNOWN: No hard constraint is known to be violated, but at least one cannot be evaluated because information is missing.

#if all hard is satisfied but all soft is violated, it is sitll feasible in this documentation
def evaluate_candidate(candidate: Mapping[str, Any], constraints: Sequence[Mapping[str, Any]]) -> CandidateEvaluation:
    """Validate and classify hard feasibility; no satisfaction numbers or optimization here.

    Missing attributes or explicit null mean UNKNOWN. UNKNOWN hard results are NOT feasible.
    The caller should enrich data or ask for clarification rather than silently pass them.
    """
    if not isinstance(candidate.get('id'), str) or not isinstance(candidate.get('attributes'), Mapping):
        raise ConstraintValidationError('Candidate requires string id and attributes object')
    evaluations = []
    attrs = candidate['attributes']
    for constraint in constraints:
        _validate_constraint(constraint)
        attr = constraint['attribute']
        actual = attrs.get(attr)
        if actual is None:
            result = 'UNKNOWN'
        else:
            try:
                satisfied = _compare(actual, constraint['operator'], constraint['value'])
            except (TypeError, ValueError) as exc:
                raise ConstraintValidationError(f"{constraint['id']}: {exc}") from exc
            result = 'SATISFIED' if satisfied else 'VIOLATED'
        evaluations.append(ConstraintEvaluation(
            constraint_id=constraint['id'], owner_id=constraint.get('owner_id'),
            kind=constraint['kind'], attribute=attr, result=result,
            actual_value=actual, expected_value=constraint['value'],
            operator=constraint['operator'], importance=constraint.get('importance'),
            tolerance_limit=(constraint.get('tolerance') or {}).get('limit'),
        ))
    hard_results = [e.result for e in evaluations if e.kind == 'hard']
    feasibility = ('INFEASIBLE' if 'VIOLATED' in hard_results else
                   'UNKNOWN' if 'UNKNOWN' in hard_results else 'FEASIBLE')
    return CandidateEvaluation(candidate_id=candidate['id'], hard_feasibility=feasibility, evaluations=tuple(evaluations))

#evaluates all candidate options
#this is what other modules will call
#eg. 20 hotels returns 20 evaluation results
def evaluate_candidates(candidates: Sequence[Mapping[str, Any]], constraints: Sequence[Mapping[str, Any]]) -> list[CandidateEvaluation]:
    seen = set()
    results = []
    ids = [c.get('id') for c in constraints]
    if len(ids) != len(set(ids)):
        raise ConstraintValidationError('Constraint IDs must be unique')
    for candidate in candidates:
        if candidate.get('id') in seen:
            raise ConstraintValidationError(f"Duplicate candidate id: {candidate.get('id')}")
        seen.add(candidate.get('id'))
        results.append(evaluate_candidate(candidate, constraints))
    return results
