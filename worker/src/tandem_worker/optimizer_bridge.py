"""SolverPort bridge to the multi-selection optimizer (optimizer/multi_solver.py).

The request comes from the generic compiler (compiler.py): decisions, rules and
needs as data, nothing trip-specific here. Preparing "no plan" explanations and
presenting the chosen plan (each person's private impact, the room-safe
summary) are still TripSolver's, so they read the same whichever chooses.

Fairness (contracts/decisions.json): each person's burden is what they pay over
the cheapest plan open to them, as a share of it. The optimizer weighs the worst
running burden (with each person's history H_i from the ledger), the total, then
cost; the chosen plan's burdens go to the ledger when it's published.
This module neither writes to SpacetimeDB nor approves.
"""
from __future__ import annotations

from optimizer.constraint_evaluator import ConstraintValidationError
from optimizer.multi_solver import MAX_COMBINATIONS, optimize_multi

from .briefs import rules_from_brief
from .compiler import compile_request
from .contracts import Conflict, ContractError, SolverResult
from .rules import Need, RoomRules, Rule, SearchFact
from .solver import TripSolver

POLICY = "cumulative_fairness_v1"


def room_rules_from(briefs) -> RoomRules:
    items = [x for b in briefs for x in rules_from_brief(b)]
    return RoomRules(rules=tuple(x for x in items if isinstance(x, Rule)), needs=tuple(x for x in items if isinstance(x, Need)),
                     facts=tuple(x for x in items if isinstance(x, SearchFact)))


class TandemOptimizerBridge(TripSolver):
    policy = POLICY

    def __init__(self, context_loader=None, history_loader=None, now=None, max_combinations=MAX_COMBINATIONS):
        super().__init__()
        self.context_loader, self.history_loader, self.now = context_loader, history_loader, now
        self.max_combinations = max_combinations
        self.last_request: dict | None = None  # what the optimizer was asked, for inspection
        self.last_response: dict | None = None

    def solve(self, snapshot, briefs, candidates, rules: RoomRules | None = None):
        if self.context_loader is not None:
            job = self.context_loader(snapshot)["job"]
            if (str(job["room_id"]) != snapshot.room_id or job["input_revision"] != snapshot.input_revision
                    or job["membership_revision"] != snapshot.membership_revision):
                raise ContractError("STALE_SNAPSHOT")
        history = dict(self.history_loader(snapshot)) if self.history_loader else dict(snapshot.compromise_history)
        options = self.prepare(snapshot, briefs, candidates)  # "no plan" explained as people know it
        if isinstance(options, SolverResult):
            return options
        rules = rules or room_rules_from(briefs).within_trip(snapshot.start_date, snapshot.end_date)
        compiled = compile_request(snapshot, rules, candidates, history, max_combinations=self.max_combinations)
        if compiled.empty:
            return SolverResult(status="INFEASIBLE", policy_version=self.policy, conflicts=tuple(
                Conflict(code="NO_CANDIDATES", owner=g.owner) for g in compiled.empty))
        self.last_request = compiled.request
        try:
            response = optimize_multi(compiled.request, cost_attribute="price_cents", max_combinations=self.max_combinations)
        except ConstraintValidationError as error:
            raise ContractError("OPTIMIZER_INPUT_INVALID") from error
        self.last_response = response
        if response["status"] == "INFEASIBLE":
            return self.no_combination(options)
        if response["status"] != "FEASIBLE":
            # NEEDS_DATA / NEEDS_REVIEW is missing evidence, not proven infeasibility.
            return SolverResult(status="UNKNOWN", policy_version=self.policy)
        picked = dict(zip((g.id for g in compiled.groups), response["proposals"][0]["candidate_ids"]))
        by_id = {c.id: c for g in compiled.groups for c in g.options}
        flight_of = {g.owner: by_id[picked[g.id]] for g in compiled.groups if g.owner is not None}
        shared_of = {g.category: by_id[picked[g.id]] for g in compiled.groups if g.owner is None}
        plan = self.finish(options, "OPTIMAL", flight_of, shared_of)  # exhaustive over every combination
        burdens = {p["participant_id"]: round(p["new_compromise_burden"], 6) for p in response["proposals"][0]["per_person"]}
        return plan.model_copy(update={"burdens": burdens})


def make_solver(policy: str, time_limit: float = 5):
    """The chooser a worker runs: TripSolver (minimax_v1) or the optimizer."""
    if policy == POLICY:
        return TandemOptimizerBridge()
    if policy == "minimax_v1":
        return TripSolver(time_limit)
    raise ContractError("UNKNOWN_SOLVER_POLICY")
