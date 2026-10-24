"""Opt-in only: these tests make bounded billable provider requests."""
import os
import pytest


@pytest.mark.live
@pytest.mark.enable_socket
@pytest.mark.skipif(os.environ.get("TANDEM_RUN_LIVE_TESTS") != "1", reason="Explicit live-provider opt-in required")
def test_real_provider_smoke(tmp_path):
    from tandem_worker.config import load_config, load_settings
    from tandem_worker.live import smoke
    config_path = os.environ.get("TANDEM_LIVE_CONFIG")
    assert config_path, "Set TANDEM_LIVE_CONFIG to an explicit one-participant config"
    config = load_config(config_path)
    settings = load_settings(os.environ.get("TANDEM_ENV_FILE", ".env"))
    result = smoke(config, settings, tmp_path)
    assert result["status"] == "OK"
    assert result["consent_count"] == 0
    assert result["usage"]["used"]["apify"] <= 1
    assert result["usage"]["used"]["claude"] <= 4
