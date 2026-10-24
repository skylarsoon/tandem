import io
from urllib.error import HTTPError

import pytest

from tandem_worker.adapters.apify import ApifyHTTP
from tandem_worker.adapters.memo23 import ACTOR_BUILD, ACTOR_ID, BUILD_ID
from tandem_worker.contracts import ContractError


def test_start_uses_bearer_auth_and_explicit_pinned_limits(monkeypatch):
    calls = []
    def send(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(b'{"data":{"id":"run1"}}')
    monkeypatch.setattr("tandem_worker.adapters.apify.urlopen", send)
    client = ApifyHTTP("secret-token")
    client.start(ACTOR_ID, ACTOR_BUILD, {"origin": "SFO"}, 1)
    request, timeout = calls[0]
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in request.full_url
    assert "build=0.0.141" in request.full_url and "maxTotalChargeUsd=1" in request.full_url
    assert timeout == 30


def test_failed_start_is_not_automatically_retried(monkeypatch):
    calls = []
    def send(request, timeout):
        calls.append(True)
        raise HTTPError(request.full_url, 429, "PRIVATE_ERROR_BODY", {}, None)
    monkeypatch.setattr("tandem_worker.adapters.apify.urlopen", send)
    with pytest.raises(ContractError, match="APIFY_RATE_LIMITED") as caught:
        ApifyHTTP("secret").start(ACTOR_ID, ACTOR_BUILD, {}, 1)
    assert "PRIVATE" not in str(caught.value)
    assert len(calls) == 1


def schema():
    fields = {name: {"type": kind} for name, kind in {"origin": "string", "destination": "string", "departDate": "string",
        "adults": "integer", "cabinClass": "string", "currency": "string", "maxItems": "integer", "sortBy": "string",
        "maxStops": "integer", "checkedBags": "integer"}.items()}
    return {"components": {"schemas": {"inputSchema": {"properties": fields}}}}


def test_preflight_checks_token_build_and_input_schema_without_starting_run(monkeypatch):
    paths = []
    client = ApifyHTTP("secret")
    def request(method, path):
        paths.append(path)
        assert method == "GET"
        if path == "users/me":
            return {"data": {"id": "account"}}
        if path.startswith("acts/"):
            return {"data": {"id": ACTOR_ID}}
        if path.endswith("openapi.json"):
            return schema()
        return {"data": {"actId": ACTOR_ID, "buildNumber": ACTOR_BUILD, "status": "SUCCEEDED"}}
    monkeypatch.setattr(client, "_request", request)
    assert client.check_actor(ACTOR_ID, ACTOR_BUILD, BUILD_ID)["schema"] == "OK"
    assert paths[0] == "users/me" and len(paths) == 4
