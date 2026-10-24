import json
import subprocess
import sys

import pytest

from tandem_worker.contracts import ContractError, ResearchRequest
from tandem_worker.live import make_providers, run_live, smoke


def kwargs(case):
    return dict(clock=case.clock, claude_client=case.claude, apify_client=case.apify)


def test_smoke_uses_live_ports_with_separate_private_and_safe_contexts(live_case):
    brief = live_case.config.briefs[0].model_copy(update={"private_reason": "PRIVATE_CANARY"})
    config = live_case.config.model_copy(update={"briefs": (brief,)})
    result = smoke(config, live_case.settings, live_case.directory, **kwargs(live_case))
    assert result["status"] == "OK"
    assert result["mode"] == "LIVE_PROVIDERS" and result["backend"] == "NONE"
    assert result["providers"] == {"model": "CLAUDE", "research": "APIFY"}
    assert result["consent_count"] == 0 and result["planning_outcome"] == "EXPLORATORY_ONLY"
    assert result["usage"]["used"] == {"claude": 2, "apify": 1}
    assert "PRIVATE_CANARY" in live_case.claude.requests[0]["messages"][0]["content"]
    assert "PRIVATE_CANARY" not in live_case.claude.requests[1]["messages"][0]["content"]
    assert "hard_cap" not in live_case.claude.requests[1]["messages"][0]["content"]
    assert "PRIVATE_CANARY" not in json.dumps(live_case.apify.starts)
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_completed_smoke_does_not_spend_again_on_rerun(live_case):
    smoke(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    result = smoke(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    assert result["cached"] is True
    assert len(live_case.claude.requests) == 2 and len(live_case.apify.starts) == 1
    live_case.clock.advance(901)
    with pytest.raises(ContractError, match="SMOKE_EVIDENCE_STALE"):
        smoke(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))


def test_actual_actor_budget_blocks_targeted_second_run(live_case):
    providers = make_providers(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    providers.preflight()
    request = ResearchRequest(purpose="INITIAL", members=("p1",), target_owners=("p1",), room_id=live_case.config.room_id,
        input_revision=1, permission_revision=1, observed_at=live_case.clock.now())
    providers.research.search(request.model_dump(mode="json"))
    targeted = request.model_copy(update={"purpose": "TARGETED", "blocker_code": "HARD_LIMIT"})
    with pytest.raises(ContractError, match="ACTOR_RUN_BUDGET_EXHAUSTED"):
        providers.research.search(targeted.model_dump(mode="json"))
    assert len(live_case.apify.starts) == 1
    assert providers.budget.report()["used"]["apify"] == 1


def test_no_actor_start_before_schema_and_access_preflight(live_case):
    providers = make_providers(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    with pytest.raises(ContractError, match="LIVE_PREFLIGHT_REQUIRED"):
        providers.research.search({})
    def incompatible(*args):
        raise ContractError("APIFY_SCHEMA_INCOMPATIBLE")
    live_case.apify.check_actor = incompatible
    with pytest.raises(ContractError, match="APIFY_SCHEMA_INCOMPATIBLE"):
        providers.preflight()
    assert live_case.apify.starts == [] and live_case.claude.requests == []


def test_live_run_requires_spacetimedb_settings(live_case):
    # Live runs always plan against SpacetimeDB (tests/backend/test_spacetime_live.py).
    with pytest.raises(ContractError, match="BACKEND_SETTINGS_MISSING"):
        run_live(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    assert live_case.apify.starts == [] and live_case.claude.requests == []


def test_budget_and_session_cannot_be_reset_by_changing_config_in_place(live_case):
    smoke(live_case.config, live_case.settings, live_case.directory, **kwargs(live_case))
    config = live_case.config.model_copy(update={"room_id": "other-room"})
    with pytest.raises(ContractError, match="LIVE_STATE_CONFIG_MISMATCH"):
        smoke(config, live_case.settings, live_case.directory, **kwargs(live_case))


@pytest.mark.parametrize("field,value", [("max_charge_usd", 2), ("max_items", 20), ("max_output_tokens", 1024)])
def test_smoke_cannot_exceed_its_advertised_small_limits(live_case, field, value):
    config = live_case.config.model_copy(update={"limits": live_case.config.limits.model_copy(update={field: value})})
    with pytest.raises(ContractError, match="SMOKE_REQUIRES_ONE_PARTICIPANT_AND_SMALL_LIMITS"):
        smoke(config, live_case.settings, live_case.directory, **kwargs(live_case))
    assert not live_case.apify.starts and not live_case.claude.requests


def test_fixture_cli_import_does_not_load_live_providers():
    code = "import sys; from tandem_worker.cli import main; assert 'tandem_worker.live' not in sys.modules; assert 'anthropic' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


def test_factory_binds_to_teammates_current_snapshot_without_backend_adapter(live_case):
    from tandem_worker.contracts import Snapshot
    snapshot = Snapshot(room_id=live_case.config.room_id, members=("p1",), input_revision=7, permission_revision=3)
    providers = make_providers(live_case.config, live_case.settings, live_case.directory, snapshot=snapshot, **kwargs(live_case))
    providers.preflight()
    request = ResearchRequest(purpose="INITIAL", members=("p1",), target_owners=("p1",), room_id=snapshot.room_id,
        input_revision=7, permission_revision=3, observed_at=live_case.clock.now())
    assert providers.research.search(request.model_dump(mode="json"))
