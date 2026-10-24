import json

import pytest

from tandem_worker.cli import main


def test_cli_local_check_reports_missing_keys_without_network(monkeypatch, capsys, live_case, tmp_path):
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "APIFY_TOKEN", "APIFY_ACTOR_BUILD"):
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "live.json"
    config.write_text(live_case.config.model_dump_json())
    monkeypatch.setattr("sys.argv", ["tandem-worker", "providers", "check", "--config", str(config), "--env-file", str(tmp_path / "absent.env")])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CONFIG_REQUIRED"
    assert "ANTHROPIC_API_KEY" in result["missing"]


def test_cli_invalid_config_has_safe_json_error(monkeypatch, capsys, tmp_path):
    config = tmp_path / "invalid.json"
    config.write_text('{"private_reason":"PRIVATE_CANARY"}')
    monkeypatch.setattr("sys.argv", ["tandem-worker", "providers", "check", "--config", str(config)])
    with pytest.raises(SystemExit):
        main()
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "INVALID_LIVE_CONFIG"
    assert "PRIVATE_CANARY" not in output
