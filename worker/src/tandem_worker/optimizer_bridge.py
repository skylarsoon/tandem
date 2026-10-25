
"""Initial SolverPort bridge for the Tandem optimizer.

This stage uses injected execution context and history.
It does not write to SpacetimeDB or approve proposals.
"""

from copy import deepcopy

from optimizer.multi_adapter import adapt_multi_execution_context
from optimizer.multi_solver import optimize_multi

from .contracts import (
    SolverResult,
    GroupSummary,
    Impact,
    ContractError,
)

POLICY = "cumulative_fairness_v1"


class TandemOptimizerBridge:
    def __init__(self, context_loader, history_loader, now=None):
        self.context_loader = context_loader
        self.history_loader = history_loader
        self.now = now

    def solve(self, snapshot, briefs, candidates):
        # 1. Get the real backend context and compromise history.
        context = deepcopy(self.context_loader(snapshot))
        history = self.history_loader(snapshot)

        # 2. Make sure the context belongs to this job.
        job = context["job"]
        if (
            str(job["room_id"]) != snapshot.room_id
            or job["input_revision"] != snapshot.input_revision
            or job["membership_revision"] != snapshot.membership_revision
        ):
            raise ContractError("STALE_SNAPSHOT")

        # 3. Respect the candidate list passed by the worker.
        # This also allows Ronit's shortlist to work independently.
        refs = {c.id for c in candidates}
        if not refs or len(refs) != len(candidates):
            raise ContractError("INVALID_CANDIDATES")

        context["candidates"] = [
            row for row in context["candidates"]
            if row["candidate_ref"] in refs
        ]

        if len(context["candidates"]) != len(refs):
            raise ContractError("INVALID_CANDIDATES")

        # 4. Translate backend data into optimizer input.
        adapted = adapt_multi_execution_context(
            context,
            task_id="group_flights",
            compromise_history=history,
            now_epoch=self.now,
        )

        # 5. Run your actual fairness-aware optimizer.
        result = optimize_multi(adapted.request)

        # 6. Translate into Skylar's existing return type.
        if result["status"] != "FEASIBLE":
            if result["status"] == "INFEASIBLE":
                return SolverResult(
                    status="INFEASIBLE",
                    policy_version=POLICY,
                )

            # NEEDS_DATA / NEEDS_REVIEW must not be
            # misrepresented as hard infeasibility.
            return SolverResult(
                status="UNKNOWN",
                policy_version=POLICY,
            )

        best = result["proposals"][0]
        selected = tuple(best["candidate_ids"])

        # Monetary impact fields are expressed in cents,
        # never in normalized fairness burden.
        by_id = {c.id: c for c in candidates}
        chosen = [by_id[cid] for cid in selected]

        impacts = []
        for c in chosen:
            own_options = [
                option.total_cents
                for option in candidates
                if option.owner == c.owner
                and option.total_cents is not None
            ]
            cheapest = min(own_options)
            impacts.append(
                Impact(
                    owner=c.owner,
                    candidate=c.id,
                    price_cents=c.total_cents,
                    cheapest_alone_cents=cheapest,
                    extra_cents=max(0, c.total_cents - cheapest),
                    flight_cents=c.total_cents,
                )
            )

        arrivals = [c.arrival_epoch for c in chosen]
        extras = [i.extra_cents for i in impacts]

        group = GroupSummary(
            total_cents=sum(c.total_cents for c in chosen),
            earliest_arrival=min(arrivals),
            latest_arrival=max(arrivals),
            arrival_window_seconds=snapshot.arrival_overlap_seconds,
            travellers_paying_extra=sum(x > 0 for x in extras),
            max_extra_cents=max(extras, default=0),
        )

        return SolverResult(
            status="OPTIMAL",
            selected=selected,
            policy_version=POLICY,
            impacts=tuple(impacts),
            group=group,
        )
