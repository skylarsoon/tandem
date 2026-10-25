import json
from types import SimpleNamespace

import pytest

from tandem_worker.adapters.claude import ClaudeModel
from tandem_worker.budget import ProviderBudget
from tandem_worker.contracts import ContractError, Decision


class HTTPFailure(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__("PRIVATE_CONTENT_MUST_NOT_BE_LOGGED")


def make_model(case, max_calls=4):
    budget = ProviderBudget(case.directory / "budget.sqlite", "test", model_calls=max_calls)
    return ClaudeModel("claude-test-model", client=case.claude, budget=budget, pause=lambda seconds: None), budget


def test_structured_parsing_and_usage_without_prompt_telemetry(live_case):
    model, budget = make_model(live_case)
    assert model.decide({"private_reason": "PRIVATE_CANARY"}) == Decision()
    request = live_case.claude.requests[0]
    assert request["output_format"] is Decision
    assert request["max_tokens"] == 512
    assert "tools" not in request
    report = budget.report()
    assert report["used"]["claude"] == 1
    assert report["calls"][0]["input_tokens"] == 20
    assert "PRIVATE_CANARY" not in json.dumps(report)


@pytest.mark.parametrize("status,code", [(401, "CLAUDE_AUTH_FAILED"), (403, "CLAUDE_AUTH_FAILED"), (404, "CLAUDE_MODEL_UNAVAILABLE"), (400, "CLAUDE_REQUEST_FAILED")])
def test_non_transient_errors_are_sanitized_without_retry(live_case, status, code):
    live_case.claude.overrides = [HTTPFailure(status)]
    model, budget = make_model(live_case)
    with pytest.raises(ContractError, match=code) as caught:
        model.decide({"private_reason": "PRIVATE_CANARY"})
    assert "PRIVATE" not in str(caught.value)
    assert len(live_case.claude.requests) == 1
    assert "PRIVATE" not in json.dumps(budget.report())


@pytest.mark.parametrize("failure", [HTTPFailure(429), HTTPFailure(503), TimeoutError("private")])
def test_transient_retry_is_counted_as_actual_message_request(live_case, failure):
    live_case.claude.overrides = [failure, failure]
    model, budget = make_model(live_case)
    assert model.decide({}) == Decision()
    assert budget.report()["used"]["claude"] == 3


def test_shared_budget_survives_new_client_and_blocks_hidden_retry(live_case):
    live_case.claude.overrides = [HTTPFailure(429)] * 4
    model, budget = make_model(live_case, max_calls=2)
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        model.decide({})
    other = ClaudeModel("claude-test-model", client=live_case.claude,
        budget=ProviderBudget(live_case.directory / "budget.sqlite", "test", model_calls=2), pause=lambda _: None)
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        other.decide({})
    assert len(live_case.claude.requests) == 2


def test_server_job_budgets_are_independent_and_survive_restart(live_case):
    model, _ = make_model(live_case, max_calls=2)
    # Existing service-wide usage must not block new jobs.
    model.decide({})
    model.decide({})
    for room, job in [(1, 1), (1, 2), (2, 1)]:
        with model.job_budget(room, job):
            assert model.decide({}) == Decision()
            assert model.decide({}) == Decision()
            with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
                model.decide({})
    restarted, _ = make_model(live_case, max_calls=2)
    with restarted.job_budget(1, 1):
        with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
            restarted.decide({})
    # Leaving a job restores the original budget.
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        model.decide({})
    assert len(live_case.claude.requests) == 8


def test_concurrent_server_jobs_keep_separate_budgets(live_case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    model, _ = make_model(live_case, max_calls=1)
    barrier = Barrier(2)

    def run(job):
        with model.job_budget(1, job):
            barrier.wait(timeout=5)
            assert model.decide({}) == Decision()
            with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
                model.decide({})

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [1, 2]))
    assert len(live_case.claude.requests) == 2


@pytest.mark.parametrize("stop,code", [("refusal", "CLAUDE_REFUSED"), ("max_tokens", "CLAUDE_TRUNCATED")])
def test_refusal_and_truncation_are_explicit(live_case, stop, code):
    live_case.claude.overrides = [SimpleNamespace(stop_reason=stop, id="test", usage=None)]
    model, _ = make_model(live_case)
    with pytest.raises(ContractError, match=code):
        model.decide({})


def test_malformed_response_uses_safe_correction_signal(live_case):
    live_case.claude.overrides = [SimpleNamespace(stop_reason="end_turn", parsed_output={"act": "APPROVE"}, usage=None, id="test")]
    model, _ = make_model(live_case)
    with pytest.raises(ValueError, match="INVALID_MODEL_OUTPUT"):
        model.decide({})


def test_access_check_uses_only_model_metadata(live_case):
    model, budget = make_model(live_case)
    assert model.check_access()["access"] == "OK"
    assert budget.report()["used"]["claude"] == 0
    assert live_case.claude.requests == []


def test_installed_sdk_structured_parsing_over_mock_http():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    requests = []
    def transport(request):
        data = json.loads(request.content)
        requests.append(data)
        assert data["output_config"]["format"]["type"] == "json_schema"
        return httpx.Response(200, json={"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test-model",
            "content": [{"type": "text", "text": json.dumps(Decision().model_dump(mode="json"))}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 20, "output_tokens": 10}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http_client:
        client = anthropic.Anthropic(api_key="test-key", http_client=http_client, max_retries=0)
        model = ClaudeModel("claude-test-model", client=client)
        assert model.decide({"candidate": {}}) == Decision()
        assert client.max_retries == 0
        assert len(requests) == 1


@pytest.mark.parametrize("stop,code", [("refusal", "CLAUDE_REFUSED"), ("max_tokens", "CLAUDE_TRUNCATED")])
def test_sdk_non_json_stop_body_is_handled_before_parsing(stop, code):
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    def transport(request):
        return httpx.Response(200, json={"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test-model",
            "content": [{"type": "text", "text": "not valid JSON"}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 10}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http_client:
        client = anthropic.Anthropic(api_key="test-key", http_client=http_client, max_retries=0)
        with pytest.raises(ContractError, match=code):
            ClaudeModel("claude-test-model", client=client).decide({})


def test_injected_sdk_client_cannot_enable_hidden_retries():
    with pytest.raises(ContractError, match="CLAUDE_SDK_RETRIES_MUST_BE_DISABLED"):
        ClaudeModel("claude-test-model", client=SimpleNamespace(max_retries=2))
