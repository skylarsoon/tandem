"""Flight-only CP-SAT solver with a fairness policy, plus an independent validator.

Policy minimax_v1, for one flight per traveller:
1. Every confirmed hard limit holds: budget cap, required checked bag, and all
   arrivals within the room's window.
2. A traveller's burden is how much more they pay than their own cheapest
   valid fare. Minimize the largest burden first, so nobody absorbs the whole
   compromise, then minimize the group's total cost.

Results explain themselves: per-traveller impacts (private), a room-safe group
summary, and, when nothing fits, which limit blocks it and by how much.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from .contracts import Conflict, GroupSummary, Impact, SolverResult, ValidationReport, digest

POLICY = "minimax_v1"
STATUS = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE",
          cp_model.MODEL_INVALID: "MODEL_INVALID", cp_model.UNKNOWN: "UNKNOWN"}


def closest_spread(options_by_owner):
    """Smallest arrival window that contains one option per traveller (sweep)."""
    events = sorted((c.arrival_epoch, owner) for owner, options in options_by_owner.items() for c in options)
    need, counts, have, best, left = len(options_by_owner), {}, 0, None, 0
    for right, (arrival, owner) in enumerate(events):
        counts[owner] = counts.get(owner, 0) + 1
        have += counts[owner] == 1
        while have == need:
            span = arrival - events[left][0]
            best = span if best is None else min(best, span)
            gone = events[left][1]
            counts[gone] -= 1
            have -= counts[gone] == 0
            left += 1
    return best


class FlightSolver:
    def __init__(self, time_limit=5):
        self.time_limit = time_limit

    def solve(self, snapshot, briefs, candidates):
        caps = {b.owner: b.hard_cap_cents for b in briefs}
        needs_bag = {b.owner for b in briefs if b.baggage_required}
        window = snapshot.arrival_overlap_seconds
        # Known to break a hard requirement → not an option. Unknown baggage stays
        # selectable so the owner's agent can object and request evidence.
        eligible = {o: [c for c in candidates if c.owner == o and c.total_cents is not None and c.total_cents <= caps[o]
                        and not (o in needs_bag and c.baggage_included is False)] for o in snapshot.members}

        conflicts = [conflict for o in snapshot.members if not eligible[o]
                     for conflict in [self._owner_conflict(o, caps[o], o in needs_bag, candidates)]]
        if conflicts:
            return SolverResult(status="INFEASIBLE", policy_version=POLICY, conflicts=tuple(conflicts))

        alone = {o: min(c.total_cents for c in eligible[o]) for o in snapshot.members}
        model = cp_model.CpModel()
        picks = {o: {c.id: model.new_bool_var(c.id) for c in eligible[o]} for o in snapshot.members}
        by_id = {c.id: c for options in eligible.values() for c in options}
        arrival, burden = {}, {}
        for o in snapshot.members:
            model.add_exactly_one(picks[o].values())
            arrival[o] = sum(by_id[i].arrival_epoch * v for i, v in picks[o].items())
            burden[o] = sum((by_id[i].total_cents - alone[o]) * v for i, v in picks[o].items())
        members = list(snapshot.members)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                model.add(arrival[a] - arrival[b] <= window)
                model.add(arrival[b] - arrival[a] <= window)
        worst = model.new_int_var(0, max(max(c.total_cents for c in eligible[o]) - alone[o] for o in members), "worst_burden")
        for o in members:
            model.add(worst >= burden[o])

        model.minimize(worst)
        status, solver = self._run(model)
        if status == "INFEASIBLE":
            spread = closest_spread(eligible)
            return SolverResult(status="INFEASIBLE", policy_version=POLICY, conflicts=(Conflict(
                code="ARRIVAL_WINDOW", closest_spread_seconds=spread, required_seconds=window),))
        if status not in {"OPTIMAL", "FEASIBLE"}:
            return SolverResult(status=status, policy_version=POLICY)
        model.add(worst <= solver.value(worst))
        model.minimize(sum(by_id[i].total_cents * v for o in members for i, v in picks[o].items()))
        second, solver = self._run(model)
        if second not in {"OPTIMAL", "FEASIBLE"}:
            return SolverResult(status=second, policy_version=POLICY)
        # A best-found (not proven optimal) stage makes the whole result FEASIBLE.
        overall = "OPTIMAL" if status == second == "OPTIMAL" else "FEASIBLE"
        chosen = {o: by_id[next(i for i, v in picks[o].items() if solver.value(v))] for o in members}
        return SolverResult(status=overall, selected=tuple(chosen[o].id for o in members), policy_version=POLICY,
            impacts=tuple(self._impact(o, chosen, alone, eligible, window) for o in members),
            group=self._summary(chosen, alone, eligible, window))

    def _run(self, model):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_search_workers = 1
        return STATUS[solver.solve(model)], solver

    @staticmethod
    def _owner_conflict(owner, cap, needs_bag, candidates):
        priced = [c for c in candidates if c.owner == owner and c.total_cents is not None]
        if needs_bag:
            with_bag = [c for c in priced if c.baggage_included is not False]
            if priced and not with_bag:
                return Conflict(code="BAGGAGE_UNAVAILABLE", owner=owner)
            priced = with_bag
        if not priced:
            return Conflict(code="NO_CANDIDATES", owner=owner)
        return Conflict(code="HARD_LIMIT", owner=owner, cheapest_cents=min(c.total_cents for c in priced))

    @staticmethod
    def _impact(owner, chosen, alone, eligible, window):
        pick = chosen[owner]
        extra = pick.total_cents - alone[owner]
        reason = "NONE"
        if extra > 0:
            # Would their own cheapest fare have broken the shared arrival rule?
            others = [c.arrival_epoch for o, c in chosen.items() if o != owner]
            cheapest = [c for c in eligible[owner] if c.total_cents == alone[owner]]
            if all(max(others + [c.arrival_epoch]) - min(others + [c.arrival_epoch]) > window for c in cheapest):
                reason = "ARRIVAL_WINDOW"
        return Impact(owner=owner, candidate=pick.id, price_cents=pick.total_cents, cheapest_alone_cents=alone[owner],
                      extra_cents=extra, reason=reason)

    @staticmethod
    def _summary(chosen, alone, eligible, window):
        arrivals = [c.arrival_epoch for c in chosen.values()]
        extras = [c.total_cents - alone[o] for o, c in chosen.items()]
        total = sum(c.total_cents for c in chosen.values())
        cheapest_total = sum(alone.values())
        cheapest_spread = None
        if cheapest_total < total:
            cheapest = {o: [c for c in eligible[o] if c.total_cents == alone[o]] for o in alone}
            cheapest_spread = closest_spread(cheapest)
        return GroupSummary(total_cents=total, earliest_arrival=min(arrivals), latest_arrival=max(arrivals),
            arrival_window_seconds=window, travellers_paying_extra=sum(1 for e in extras if e > 0),
            max_extra_cents=max(extras), cheapest_total_cents=cheapest_total if cheapest_total < total else None,
            cheapest_spread_seconds=cheapest_spread)


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
            if candidate.cost_basis != "COMPLETE_FLIGHT_TOTAL" or candidate.missing_critical_fields:
                checks.append("INCOMPLETE_COST_EVIDENCE")
            if brief.baggage_required and candidate.baggage_included is not True:
                checks.append("BAGGAGE_UNKNOWN" if candidate.baggage_included is None else "BAGGAGE_REQUIRED")
        if records and max(c.arrival_epoch for c in records) - min(c.arrival_epoch for c in records) > snapshot.arrival_overlap_seconds:
            checks.append("ARRIVAL_OVERLAP")
        return ValidationReport(valid=not checks, snapshot_ref=digest(snapshot), candidate_digest=digest([c.model_dump(mode="json") for c in candidates]),
            selected=selected, checks=tuple(sorted(set(checks))))
