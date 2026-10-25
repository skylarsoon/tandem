"""Elena's multi-selection optimizer as the worker's solver, end to end: research,
shortlist, optimizer, agents, validation and publication (local backend)."""
import pytest

from tandem_worker.optimizer_bridge import TandemOptimizerBridge


@pytest.mark.parametrize("scenario,outcome", [("happy", "AWAITING_REVIEW"), ("trip-happy", "AWAITING_REVIEW"),
                                               ("group-arrival", "WAITING_FOR_INPUT"),  # asks before anyone pays extra, like TripSolver
                                               ("arrival-impossible", "NO_FEASIBLE_CANDIDATE")])
def test_the_optimizer_runs_the_whole_planning_flow(setup_worker, scenario, outcome):
    backend, worker, job, *_ = setup_worker(scenario, solver=TandemOptimizerBridge())
    assert worker.run_one(job)["outcome"] == outcome
    if outcome == "NO_FEASIBLE_CANDIDATE":
        [event] = [e for e in backend.shared_report()["events"] if e["kind"] == "NO_FEASIBLE_PLAN"]
        assert event["payload"]["blocked_travellers"] or event["payload"].get("closest_spread_seconds"), "explained, not silent"
