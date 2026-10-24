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


@pytest.fixture
def live_case(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from tandem_worker.config import LiveConfig, Settings
    from tandem_worker.contracts import Decision, VoiceDraft, VoiceSegment

    config = LiveConfig.model_validate_json((Path(__file__).parents[1] / "live.example.json").read_text())
    settings = Settings(anthropic_api_key="private-test-key", claude_model="claude-test-model", apify_token="private-apify-token")
    clock = FakeClock(1_790_000_000)

    class ClaudeClient:
        def __init__(self):
            self.requests = []
            self.overrides = []
            self.metadata_requests = []
            self.messages = SimpleNamespace(parse=self.parse)
            self.models = SimpleNamespace(retrieve=self.retrieve)

        def retrieve(self, model, **kwargs):
            self.metadata_requests.append(model)
            return SimpleNamespace(id=model)

        def parse(self, **kwargs):
            self.requests.append(kwargs)
            if self.overrides:
                value = self.overrides.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value
            context = json.loads(kwargs["messages"][0]["content"])
            if kwargs["output_format"] is Decision:
                result = Decision()
            else:
                result = VoiceDraft(segments=tuple(VoiceSegment(fact_ref=k, text=v) for k, v in context["facts"].items()))
            return SimpleNamespace(id="message-test", _request_id="request-test", stop_reason="end_turn", parsed_output=result,
                usage=SimpleNamespace(input_tokens=20, output_tokens=10))

    class ApifyClient:
        def __init__(self):
            self.starts, self.preflights = [], []
            self.rows = [{"record-type": "flight", "price": "120.25", "currency": "USD", "carrier-code": "AA",
                "from-code": "SFO", "to-code": "LAX", "depart-date": "2027-01-15", "depart-time": "10:00",
                "arrive-date": "2027-01-15", "arrive-time": "11:25", "checked-bags-included": 0, "passengers": "1 adult",
                "segments-0-flight-number": "AA123", "google-flights-url": "https://www.google.com/travel/flights/booking?tfs=test"}]

        def check_actor(self, actor_id, build, build_id):
            self.preflights.append((actor_id, build, build_id))
            return {"access": "OK", "schema": "OK", "build": build}

        def start(self, actor_id, build, actor_input, max_charge_usd):
            self.starts.append((actor_id, build, actor_input, max_charge_usd))
            return {"id": "run-test", "defaultDatasetId": "dataset-test"}

        def run(self, run_id):
            return {"status": "SUCCEEDED", "defaultDatasetId": "dataset-test"}

        def items(self, dataset_id, offset, limit):
            return self.rows[offset:offset + limit]

    return SimpleNamespace(config=config, settings=settings, clock=clock, claude=ClaudeClient(), apify=ApifyClient(), directory=tmp_path)
