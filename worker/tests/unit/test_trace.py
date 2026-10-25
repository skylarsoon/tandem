"""The transparent optimizer runs double as a check of the optimizer itself."""
import pytest

from tandem_worker.fixtures import FRICTION
from tandem_worker.trace import render, scenario, trace

NAMES = ["happy", *FRICTION, "fair-split"]


@pytest.mark.parametrize("name", NAMES)
def test_every_scenario_is_explained_and_the_winner_checks_out(name):
    t = trace(*scenario(name))
    feasible = lambda r: r["status"] in ("OPTIMAL", "FEASIBLE")
    assert feasible(t["optimizer"]) == feasible(t["trip_solver"]), t["agree"]
    if "recount" in t and feasible(t["optimizer"]):
        assert sorted(t["recount"]["best"]["candidate_ids"]) == sorted(t["optimizer"]["selected"]), \
            "the optimizer's pick is the best combination the independent recount finds, under the same scoring"
        assert t["recount"]["valid"] >= 1 and t["ranking"][0]["score"]["total"] >= 0
    assert render(name, t).startswith(f"## {name}")


def test_the_shortlist_says_why_options_were_left_out():
    t = trace(*scenario("budget-blocked"))
    [p1] = [s for s in t["shortlist"] if s["person"] == "p1"]
    assert p1["usable"] == 0 and p1["left_out"] == {"RULE:total_cents": 1}


def test_fairness_shares_the_compromise():
    """The cheapest plan would put the whole compromise on c ($90 more); the optimizer
    shares it ($50 each for a and b), as TripSolver's minimax does."""
    t = trace(*scenario("fair-split"))
    assert t["optimizer"]["total_cents"] == t["trip_solver"]["total_cents"] == 40_000
    assert t["ranking"][0]["burden"] == {"a": 0.5, "b": 0.5, "c": 0.0}
    assert t["agree"] == "AGREE: same plan"
