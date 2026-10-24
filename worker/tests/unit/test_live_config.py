import json
from datetime import date

import pytest

from tandem_worker.config import Settings, load_config, load_settings
from tandem_worker.contracts import ContractError
from tandem_worker.live import check_providers


def test_env_file_never_overrides_process_environment(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "APIFY_TOKEN", "APIFY_ACTOR_BUILD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_MODEL", "process-model")
    file = tmp_path / ".env"
    file.write_text('CLAUDE_MODEL=file-model\nANTHROPIC_API_KEY="private$literal"\nAPIFY_TOKEN=file-token\n')
    settings = load_settings(file)
    assert settings.claude_model == "process-model"
    assert settings.anthropic_api_key.get_secret_value() == "private$literal"
    assert "private$literal" not in repr(settings)


def test_existing_local_key_names_are_supported_without_global_export(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "APIFY_TOKEN", "APIFY_API_KEY", "CLAUDE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env.local"
    path.write_text("CLAUDE_API_KEY=local-claude\nAPIFY_API_KEY=local-apify\nCLAUDE_MODEL=claude-test-model\n")
    settings = load_settings(path)
    assert settings.anthropic_api_key.get_secret_value() == "local-claude"
    assert settings.apify_token.get_secret_value() == "local-apify"
    assert __import__('os').environ.get("CLAUDE_API_KEY") is None


def test_process_alias_outranks_canonical_file_value(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "process-key")
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=file-key\n")
    assert load_settings(path).anthropic_api_key.get_secret_value() == "process-key"


def test_default_env_falls_back_to_local_file_in_current_directory(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "APIFY_TOKEN", "APIFY_API_KEY", "CLAUDE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.local").write_text("CLAUDE_API_KEY=local-key\nAPIFY_API_KEY=apify-key\nCLAUDE_MODEL=claude-test\n")
    assert load_settings().missing() == []


def test_missing_settings_report_only_variable_names(live_case):
    result = check_providers(live_case.config, Settings(), clock=live_case.clock)
    assert result["status"] == "CONFIG_REQUIRED"
    assert result["missing"] == ["ANTHROPIC_API_KEY", "CLAUDE_MODEL", "APIFY_TOKEN"]
    assert result["message_requests"] == result["actor_runs"] == 0


def test_online_preflight_has_no_billable_generation_or_actor_start(live_case):
    result = check_providers(live_case.config, live_case.settings, online=True, clock=live_case.clock,
        claude_client=live_case.claude, apify_client=live_case.apify)
    assert result["status"] == "OK"
    assert live_case.apify.preflights
    assert live_case.claude.requests == [] and live_case.apify.starts == []
    assert "private-test-key" not in json.dumps(result)


def test_invalid_configuration_does_not_echo_rejected_input(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"api_key":"PRIVATE_CANARY"}')
    with pytest.raises(ContractError, match="INVALID_LIVE_CONFIG") as caught:
        load_config(path)
    assert "PRIVATE_CANARY" not in str(caught.value)


def test_unknown_build_does_not_fall_back_to_latest(live_case):
    settings = live_case.settings.model_copy(update={"apify_actor_build": "latest"})
    with pytest.raises(ContractError, match="UNSUPPORTED_ACTOR_BUILD"):
        check_providers(live_case.config, settings, clock=live_case.clock)


def test_past_date_is_rejected_before_network_access(live_case):
    search = live_case.config.searches[0].model_copy(update={"departure_date": date(2020, 1, 1)})
    config = live_case.config.model_copy(update={"searches": (search,)})
    with pytest.raises(ContractError, match="FUTURE_DEPARTURE_DATE_REQUIRED"):
        check_providers(config, live_case.settings, online=True, clock=live_case.clock,
            claude_client=live_case.claude, apify_client=live_case.apify)
    assert not live_case.claude.metadata_requests and not live_case.apify.preflights
