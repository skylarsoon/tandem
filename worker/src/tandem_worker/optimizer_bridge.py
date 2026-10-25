"""SolverPort bridge to the multi-selection optimizer (optimizer/multi_solver.py).

The trip knowledge stays on this side: which parts make a trip (a flight per
traveller, the stay, the transfer), who pays what (own flight plus an equal
share), the room's arrival window and each person's whole-trip limit become
the optimizer's generic request (selection groups, aggregates, hard
constraints). The optimizer ranks every combination. Preparing the options and
explaining the outcome are TripSolver's, so plans and "no plan" read the same
whichever chooses.

Burden is cost-only for now: onboarding records hard limits, not weighted soft
preferences, so every combination adds the same (zero) new burden and the
cheapest valid one wins. Each person's cumulative burden (H_i, from the
compromise ledger) is passed through, so fairness applies as soon as soft
preferences exist. This module neither writes to SpacetimeDB nor approves.
"""
from __future__ import annotations

from math import prod

from optimizer.constraint_evaluator import ConstraintValidationError
from optimizer.multi_solver import MAX_COMBINATIONS, optimize_multi

from .contracts import ContractError, SolverResult
from .solver import TripSolver

POLICY = "cumulative_fairness_v1"
SHARED = ("STAY", "TRANSFER")


class TandemOptimizerBridge(TripSolver):
    policy = POLICY

    def __init__(self, context_loader=None, history_loader=None, now=None, max_combinations=MAX_COMBINATIONS):
        super().__init__()
        self.context_loader, self.history_loader, self.now = context_loader, history_loader, now
        self.max_combinations = max_combinations
        self.last_request: dict | None = None  # what the optimizer was asked, for inspection
        self.last_response: dict | None = None

    def solve(self, snapshot, briefs, candidates):
        if self.context_loader is not None:
            job = self.context_loader(snapshot)["job"]
            if (str(job["room_id"]) != snapshot.room_id or job["input_revision"] != snapshot.input_revision
                    or job["membership_revision"] != snapshot.membership_revision):
                raise ContractError("STALE_SNAPSHOT")
        self.history = dict(self.history_loader(snapshot)) if self.history_loader else dict(snapshot.compromise_history)
        return super().solve(snapshot, briefs, candidates)

    def choose(self, o):
        flight_count = prod(len(o.flights[m]) for m in o.members)
        if flight_count > self.max_combinations:
            # The shortlist keeps this small; never truncate silently here.
            raise ContractError("TOO_MANY_COMBINATIONS")
        # A cheaper stay or transfer never makes a combination worse when cost is the
        # objective (it lowers everyone's share and leaves arrivals unchanged), so the
        # cheapest options that fit the combination budget lose nothing.
        per_part = int((self.max_combinations // flight_count) ** (1 / len(o.shared))) if o.shared else 0
        shared = {k: sorted(options, key=lambda c: (c.total_cents, c.id))[:max(1, per_part)] for k, options in o.shared.items()}
        request = self.request(o, shared)
        self.last_request = request
        try:
            response = optimize_multi(request, cost_attribute="price_cents", max_combinations=self.max_combinations)
        except ConstraintValidationError as error:
            raise ContractError("OPTIMIZER_INPUT_INVALID") from error
        self.last_response = response
        if response["status"] == "INFEASIBLE":
            return self.no_combination(o)
        if response["status"] != "FEASIBLE":
            # NEEDS_DATA / NEEDS_REVIEW is missing evidence, not proven infeasibility.
            return SolverResult(status="UNKNOWN", policy_version=self.policy)
        by_id = {c.id: c for options in [*o.flights.values(), *shared.values()] for c in options}
        chosen = [by_id[i] for i in response["proposals"][0]["candidate_ids"]]
        flight_of = {m: chosen[i] for i, m in enumerate(o.members)}
        shared_of = dict(zip(shared, chosen[len(o.members):]))
        return "OPTIMAL", flight_of, shared_of  # exhaustive over every combination

    def request(self, o, shared):
        """The trip as a generic multi-selection request. Group ids are positional
        because traveller names can contain any character."""
        n, flight_groups = o.n, [f"flight_{i}" for i in range(len(o.members))]
        groups, candidates = [], []
        for gid, m in zip(flight_groups, o.members):
            groups.append({"id": gid, "required_count": 1, "candidate_ids": [c.id for c in o.flights[m]]})
            # person_cost is party-size scaled (n × own flight; shared items at their total),
            # so a traveller's sum compares exactly against n × their limit.
            candidates += [{"id": c.id, "attributes": {"price_cents": c.total_cents, "person_cost": n * c.total_cents,
                                                       "arrival_epoch": c.arrival_epoch}} for c in o.flights[m]]
        for k, options in shared.items():
            groups.append({"id": k.lower(), "required_count": 1, "candidate_ids": [c.id for c in options]})
            candidates += [{"id": c.id, "attributes": {"price_cents": c.total_cents, "person_cost": c.total_cents}}
                           for c in options]
        rules = [{"attribute": "arrival_spread", "operation": "spread", "source_attribute": "arrival_epoch",
                  "selection_group_ids": flight_groups}] if n > 1 else []
        constraints = [{"id": "arrival_window", "kind": "hard", "attribute": "arrival_spread", "operator": "lte",
                        "value": o.window}] if n > 1 else []
        for i, m in enumerate(o.members):
            cap = o.by_owner[m].trip_cap_cents
            if cap is None:
                continue
            rules.append({"attribute": f"trip_cost_{i}", "operation": "sum", "source_attribute": "person_cost",
                          "selection_group_ids": [flight_groups[i], *(k.lower() for k in shared)]})
            constraints.append({"id": f"trip_limit_{i}", "kind": "hard", "owner_id": m, "attribute": f"trip_cost_{i}",
                                "operator": "lte", "value": n * cap})
        return {"schema_version": "1.0", "session_id": o.room_id, "task_id": "trip", "task_type": "multi_selection",
                "revision": o.revision, "participants": [{"id": m} for m in o.members], "candidates": candidates,
                "constraints": constraints,
                "compromise_history": {m: float(self.history.get(m, 0.0)) for m in o.members},
                "locked_candidate_ids": [], "max_results": 3, "selection_groups": groups, "aggregate_rules": rules}


def make_solver(policy: str, time_limit: float = 5):
    """The chooser a worker runs: TripSolver (minimax_v1) or the optimizer."""
    if policy == POLICY:
        return TandemOptimizerBridge()
    if policy == "minimax_v1":
        return TripSolver(time_limit)
    raise ContractError("UNKNOWN_SOLVER_POLICY")
