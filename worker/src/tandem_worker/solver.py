"""Minimal real flight-only CP-SAT adapter plus an independent validator.

flight_cost_v1 minimizes total known cost. It is not the unapproved minimax
fairness policy. Ronit's richer adapter can replace it through SolverPort.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .contracts import SolverResult, ValidationReport, digest


class FlightSolver:
    def __init__(self, time_limit=5):
        self.time_limit = time_limit

    def solve(self, snapshot, briefs, candidates):
        model = cp_model.CpModel()
        caps = {b.owner: b.hard_cap_cents for b in briefs}
        eligible = [c for c in candidates if c.owner in caps and c.total_cents is not None and c.total_cents <= caps[c.owner]]
        variables = {c.id: model.new_bool_var(c.id) for c in eligible}
        for owner in snapshot.members:
            model.add(sum(variables[c.id] for c in eligible if c.owner == owner) == 1)
        for i, a in enumerate(eligible):
            for b in eligible[i + 1:]:
                if a.owner != b.owner and abs(a.arrival_epoch - b.arrival_epoch) > snapshot.arrival_overlap_seconds:
                    model.add(variables[a.id] + variables[b.id] <= 1)
        model.minimize(sum(c.total_cents * variables[c.id] for c in eligible))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_search_workers = 1
        status = solver.solve(model)
        names = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "MODEL_INVALID", cp_model.UNKNOWN: "UNKNOWN"}
        selected = tuple(c.id for c in eligible if solver.value(variables[c.id])) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else ()
        return SolverResult(status=names[status], selected=selected)


class FlightValidator:
    def validate(self, snapshot, briefs, candidates, selected, now):
        checks = []
        by_id = {c.id: c for c in candidates}
        records = [by_id[r] for r in selected if r in by_id]
        if len(set(selected)) != len(selected) or len(records) != len(snapshot.members) or {c.owner for c in records} != set(snapshot.members):
            checks.append("INVALID_COVERAGE")
        by_owner = {b.owner: b for b in briefs}
        for candidate in records:
            brief = by_owner[candidate.owner]
            if candidate.total_cents is None or candidate.total_cents > brief.hard_cap_cents:
                checks.append("HARD_COST")
            if candidate.observed_at > now or candidate.valid_until <= now:
                checks.append("EVIDENCE_EXPIRED")
            if brief.baggage_required and candidate.baggage_included is not True:
                checks.append("BAGGAGE_UNKNOWN")
        if records and max(c.arrival_epoch for c in records) - min(c.arrival_epoch for c in records) > snapshot.arrival_overlap_seconds:
            checks.append("ARRIVAL_OVERLAP")
        return ValidationReport(valid=not checks, snapshot_ref=digest(snapshot), candidate_digest=digest([c.model_dump(mode="json") for c in candidates]),
            selected=selected, checks=tuple(sorted(set(checks))))
