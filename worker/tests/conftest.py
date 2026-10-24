import os

import pytest

from tandem_worker.backend import LocalBackend
from tandem_worker.contracts import Limits
from tandem_worker.fixtures import FixtureResearch, ScriptedModel, scenario_inputs
from tandem_worker.ports import FakeClock
from tandem_worker.runner import Worker


# Real personal data must not be exported by an inherited tracing environment.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@pytest.fixture
def setup_worker(tmp_path):
    def create(scenario="happy", *, model=None, limits=None, briefs=None, validator=None, solver=None):
        clock = FakeClock()
        limits = limits or Limits()
        backend = LocalBackend(tmp_path / "backend.sqlite", clock, limits)
        snapshot, defaults = scenario_inputs(scenario)
        job = backend.seed(snapshot, briefs or defaults)
        model = model or ScriptedModel(scenario)
        research = FixtureResearch(clock, scenario)
        worker = Worker(backend, model, research, tmp_path / "checkpoints.sqlite", clock=clock,
                        limits=limits, validator=validator, solver=solver)
        return backend, worker, job, model, research, clock
    return create
