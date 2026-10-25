from types import SimpleNamespace

from tandem_worker.adapters.claude import ClaudeModel
from tandem_worker.budget import ProviderBudget
from tandem_worker.contracts import Decision
from tandem_worker.serve import ServeConfig, serve


def test_room_server_assigns_a_durable_budget_per_job(live_case, monkeypatch):
    budget = ProviderBudget(live_case.directory / "providers.sqlite", "serve", model_calls=1)
    model = ClaudeModel("claude-test-model", client=live_case.claude, budget=budget)
    # Simulate a service directory that has already exhausted its old shared cap.
    model.decide({})

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def run_one(self, job_id):
            assert model.decide({}) == Decision()
            return {"outcome": "OK"}

    monkeypatch.setattr("tandem_worker.runner.Worker", Worker)
    jobs = [{"id": n, "room_id": 1, "kind": kind} for n, kind in enumerate(["PROFILE", "INTAKE", "PLAN"])]
    results = serve(ServeConfig(), SimpleNamespace(require_backend=lambda: None), live_case.directory,
        backend=SimpleNamespace(pending_jobs=lambda: jobs), once=True,
        providers=SimpleNamespace(model=model, research=SimpleNamespace(verified=True)))
    assert len(results) == 3
    assert all(r["outcome"] == "OK" for r in results)
    assert len(live_case.claude.requests) == 4
