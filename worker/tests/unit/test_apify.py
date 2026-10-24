from datetime import date

import pytest

from tandem_worker.adapters.apify import ApprovedFlightSearch, ApifyFlightResearch, RunLedger
from tandem_worker.contracts import Candidate, ContractError
from tandem_worker.ports import FakeClock


class Client:
    def __init__(self):
        self.starts = 0
        self.polls = 0
        self.pages = []

    def start(self, *args):
        self.starts += 1
        return {"id": "run1", "defaultDatasetId": "dataset1"}

    def run(self, run_id):
        self.polls += 1
        return {"status": "SUCCEEDED", "defaultDatasetId": "dataset1"}

    def items(self, dataset_id, offset, limit):
        self.pages.append((offset, limit))
        return [{"price": 42000}] if offset == 0 else []


def setup_gateway(tmp_path, client=None, max_polls=2):
    clock = FakeClock()
    client = client or Client()
    def normalize(item, search, run):
        return Candidate(id="observed-1", owner=search.owner, total_cents=item["price"], arrival_epoch=1800010000,
            observed_at=run["observed_at"], valid_until=run["observed_at"] + 60,
            source_url="https://source.invalid/flight", run_ref=run["run_id"], mode="APIFY_OBSERVED", cost_basis="COMPLETE_FLIGHT_TOTAL")
    gateway = ApifyFlightResearch(client, RunLedger(tmp_path / "runs.sqlite"), actor_id="allowlisted-actor", build="1.2.3",
        room_id="r", input_revision=1, permission_revision=1, clock=clock,
        approved_searches=[ApprovedFlightSearch(owner="p1", origin="SFO", destination="LAX", departure_date=date(2027, 1, 1))],
        build_input=lambda search, purpose: {"origin": search.origin, "destination": search.destination, "purpose": purpose},
        normalize_item=normalize, max_polls=max_polls, pause=lambda seconds: None)
    request = {"room_id": "r", "input_revision": 1, "permission_revision": 1, "members": ["p1"], "purpose": "INITIAL"}
    return gateway, client, request


def test_run_id_and_dataset_reused_without_second_submission(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    results = gateway.search(request)
    assert gateway.search(request) == results
    assert client.starts == 1
    assert results[0].mode == "APIFY_OBSERVED"
    assert results[0].run_ref == "run1"


def test_ambiguous_acceptance_never_automatically_starts_again(tmp_path):
    class Ambiguous(Client):
        def start(self, *args):
            self.starts += 1
            raise SystemExit()
    gateway, client, request = setup_gateway(tmp_path, Ambiguous())
    with pytest.raises(SystemExit):
        gateway.search(request)
    with pytest.raises(ContractError, match="APIFY_START_AMBIGUOUS"):
        gateway.search(request)
    assert client.starts == 1


def test_known_run_is_resumed_after_dataset_failure(tmp_path):
    class FailOnce(Client):
        def items(self, *args):
            if not self.pages:
                self.pages.append("failed")
                raise RuntimeError("synthetic failure")
            return super().items(*args)
    gateway, client, request = setup_gateway(tmp_path, FailOnce(), max_polls=1)
    with pytest.raises(RuntimeError):
        gateway.search(request)
    assert gateway.search(request)
    assert client.starts == client.polls == 1


def test_poll_budget_survives_repeated_calls(tmp_path):
    class Running(Client):
        def run(self, run_id):
            self.polls += 1
            return {"status": "RUNNING"}
    gateway, client, request = setup_gateway(tmp_path, Running())
    for _ in range(2):
        with pytest.raises(ContractError, match="APIFY_POLL_BUDGET_EXHAUSTED"):
            gateway.search(request)
    assert client.starts == 1
    assert client.polls == 2


def test_search_does_not_receive_private_reason_or_cap(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    seen = []
    gateway.build_input = lambda search, purpose: seen.append(search.model_dump()) or {"origin": search.origin}
    gateway.search({**request, "private_reason": "INJECTED", "hard_cap": 1})
    assert "private_reason" not in seen[0] and "hard_cap" not in seen[0]


def test_stale_search_authority_rejected_before_external_call(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    with pytest.raises(ContractError, match="STALE_SEARCH_AUTHORITY"):
        gateway.search({**request, "permission_revision": 2})
    assert client.starts == 0


def test_bad_actor_item_is_quarantined_not_fabricated(tmp_path):
    gateway, client, request = setup_gateway(tmp_path)
    gateway.normalize_item = lambda *args: {"unexpected": "schema changed"}
    assert gateway.search(request) == []
