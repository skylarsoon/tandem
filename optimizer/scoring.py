"""Soft-preference scoring for the Tandem universal optimizer (schema v1).

This module does not choose a candidate, run CP-SAT, or modify session history.
Use constraint_evaluator.py first to classify hard feasibility. Only FEASIBLE
candidates should be sent to a solver for selection.

Satisfaction rules:
* For ordinary soft comparisons: 1.0 if satisfied, otherwise 0.0.
* For numeric lt/lte/gt/gte with tolerance.limit: 1.0 on the preferred
  side, 0.0 at/beyond the tolerance limit, and linear in between.
* Missing/null candidate attributes: satisfaction UNKNOWN (None), never zero.
* Without user preferences: current-task burden is 0, satisfaction is None.

Missing preferences are excluded from the known-weight denominator, but
coverage is reported so the solver/orchestrator can require enrichment before
comparing proposals. Unknown data must not be silently treated as satisfied.
"""

#The existing implementation calculates group satisfaction by averaging only participants who have at least one evaluable soft preference.
#So 0.444 shouldn't be interpreted as "44.4% of the group is satisfied."
#It's the average soft-preference satisfaction among the three participants who expressed evaluable preferences.

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

try:
    from .constraint_evaluator import (
        ConstraintValidationError, evaluate_candidate,
    )
except ImportError:  # Allows running directly inside the optimizer/ folder.
    from constraint_evaluator import (
        ConstraintValidationError, evaluate_candidate,
    )


@dataclass(frozen=True)
class PreferenceScore:
    constraint_id: str
    owner_id: str | None
    importance: int
    satisfaction: float | None
    weighted_penalty: float | None
    result: str  # SATISFIED | VIOLATED | UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParticipantScore:
    participant_id: str
    satisfaction: float | None
    new_compromise_burden: float
    historical_compromise_burden: float
    projected_total_burden: float
    known_weight: int
    total_weight: int
    coverage: float  # known importance / total importance; 1 if none specified
    unknown_preference_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['unknown_preference_ids'] = list(self.unknown_preference_ids)
        return d


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    hard_feasibility: str
    per_person: tuple[ParticipantScore, ...]
    preferences: tuple[PreferenceScore, ...]
    group_satisfaction: float | None
    fairness: float | None  # 1 / (1 + highest projected session burden)
    max_projected_burden: float
    total_new_compromise_burden: float
    all_soft_preferences_known: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            'candidate_id': self.candidate_id,
            'hard_feasibility': self.hard_feasibility,
            'per_person': [p.to_dict() for p in self.per_person],
            'preferences': [p.to_dict() for p in self.preferences],
            'group_satisfaction': self.group_satisfaction,
            'fairness': self.fairness,
            'max_projected_burden': self.max_projected_burden,
            'total_new_compromise_burden': self.total_new_compromise_burden,
            'all_soft_preferences_known': self.all_soft_preferences_known,
        }


def _satisfaction(actual: Any, constraint: Mapping[str, Any], satisfied: bool) -> float:
    """Compute 0..1 satisfaction for a known candidate attribute."""
    if satisfied:
        return 1.0
    tol = constraint.get('tolerance')
    if tol is None:
        return 0.0
    preferred = constraint['value']
    limit = tol['limit']
    if type(actual) not in (int, float) or not isfinite(actual):
        raise ConstraintValidationError(
            f"{constraint['id']}: numeric tolerance requires a finite candidate value"
        )
    # The evaluator already validates numeric preferred/limit and their order.
    if constraint['operator'] in ('lte', 'lt'):
        return max(0.0, min(1.0, (limit - actual) / (limit - preferred)))
    return max(0.0, min(1.0, (actual - limit) / (preferred - limit)))

#Calculates each person's normalized compromise burden
#Calculates group satisfaction and the maximum projected burden
def score_candidate(
    candidate: Mapping[str, Any],
    constraints: Sequence[Mapping[str, Any]],
    participants: Sequence[Mapping[str, Any]],
    compromise_history: Mapping[str, float] | None = None,
) -> CandidateScore:
    """Score candidate according to schema v1. Does not make it eligible.

    Requirements: each soft preference must have a registered owner and an
    integer importance of 1..5 (enforced by the existing evaluator).
    A candidate with incomplete soft data reports uncertainty explicitly.
    """
    evaluation = evaluate_candidate(candidate, constraints)
    ids = [p.get('id') for p in participants]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
        raise ConstraintValidationError('Participants must have unique nonempty string ids')
    history = compromise_history or {}
    if set(history) - set(ids):
        raise ConstraintValidationError('Compromise history contains unknown participant ids')
    for uid, v in history.items():
        if type(v) not in (int, float) or not isfinite(v) or v < 0:
            raise ConstraintValidationError(f'{uid}: history must be nonnegative and finite')

    constraint_map = {c['id']: c for c in constraints}
    if len(constraint_map) != len(constraints):
        raise ConstraintValidationError('Constraint IDs must be unique')
    score_rows: list[PreferenceScore] = []
    for ev in evaluation.evaluations:
        if ev.kind != 'soft':
            continue
        if ev.owner_id not in ids:
            raise ConstraintValidationError(
                f'{ev.constraint_id}: soft preferences require a known participant owner'
            )
        s = None if ev.result == 'UNKNOWN' else _satisfaction(
            ev.actual_value, constraint_map[ev.constraint_id], ev.result == 'SATISFIED'
        )
        weight = ev.importance
        score_rows.append(PreferenceScore(
            constraint_id=ev.constraint_id, owner_id=ev.owner_id,
            importance=weight, satisfaction=s,
            weighted_penalty=None if s is None else weight * (1 - s),
            result=ev.result,
        ))

    individual: list[ParticipantScore] = []
    for uid in ids:
        prefs = [p for p in score_rows if p.owner_id == uid]
        total_weight = sum(p.importance for p in prefs)
        known = [p for p in prefs if p.satisfaction is not None]
        known_weight = sum(p.importance for p in known)
        # Avoid interpreting missing data as a satisfied preference. Partial
        # aggregates are descriptive only; caller must check coverage.
        burden = (sum(p.weighted_penalty for p in known) / known_weight
                  if known_weight else 0.0)
        satisfaction = (1 - burden if known_weight else None)
        prev = float(history.get(uid, 0.0))
        individual.append(ParticipantScore(
            participant_id=uid, satisfaction=satisfaction,
            new_compromise_burden=burden, historical_compromise_burden=prev,
            projected_total_burden=prev + burden,
            known_weight=known_weight, total_weight=total_weight,
            coverage=known_weight / total_weight if total_weight else 1.0,
            unknown_preference_ids=tuple(
                p.constraint_id for p in prefs if p.satisfaction is None
            ),
        ))

    observed = [p.satisfaction for p in individual if p.satisfaction is not None]
    group_sat = sum(observed) / len(observed) if observed else None
    maximum = max((p.projected_total_burden for p in individual), default=0.0)
    all_known = all(p.coverage == 1 for p in individual)
    return CandidateScore(
        candidate_id=evaluation.candidate_id,
        hard_feasibility=evaluation.hard_feasibility,
        per_person=tuple(individual), preferences=tuple(score_rows),
        group_satisfaction=group_sat,
        fairness=1 / (1 + maximum), max_projected_burden=maximum,
        total_new_compromise_burden=sum(p.new_compromise_burden for p in individual),
        all_soft_preferences_known=all_known,
    )


def score_candidates(
    candidates: Sequence[Mapping[str, Any]],
    constraints: Sequence[Mapping[str, Any]],
    participants: Sequence[Mapping[str, Any]],
    compromise_history: Mapping[str, float] | None = None,
) -> list[CandidateScore]:
    """Score all candidates; the caller must filter hard_feasibility == FEASIBLE."""
    ids = [c.get('id') for c in candidates]
    if len(ids) != len(set(ids)):
        raise ConstraintValidationError('Candidate IDs must be unique')
    return [score_candidate(c, constraints, participants, compromise_history)
            for c in candidates]

#calculates what each person's new compromise history would be after approving a candidate
def approved_history_update(score: CandidateScore) -> dict[str, float]:
    """Compute a NEW compromise ledger after explicit approval.

    This is pure: it neither mutates the previous ledger nor saves anything.
    Only call after approval, and avoid counting the same approved task twice.
    """
    if score.hard_feasibility != 'FEASIBLE' or not score.all_soft_preferences_known:
        raise ValueError('Cannot commit history for infeasible or incompletely scored option')
    return {p.participant_id: p.projected_total_burden for p in score.per_person}
