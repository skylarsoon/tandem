import pytest
from pydantic import ValidationError

from tandem_worker.budget import ProviderBudget
from tandem_worker.config import LiveLimits
from tandem_worker.contracts import ContractError
from tandem_worker.serve import ServeConfig


def test_a_service_budget_resets_each_day(tmp_path):
    today = ["2026-10-25"]
    budget = ProviderBudget(tmp_path / "budget.sqlite", "serve", model_calls=1, daily=True, day=lambda: today[0])
    budget.reserve("claude")
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        budget.reserve("claude")
    today[0] = "2026-10-26"
    assert budget.reserve("claude") == 1
    assert budget.report()["used"]["claude"] == 1


def test_a_one_off_run_keeps_one_count(tmp_path):
    budget = ProviderBudget(tmp_path / "budget.sqlite", "run", model_calls=1)
    budget.reserve("claude")
    again = ProviderBudget(tmp_path / "budget.sqlite", "run", model_calls=1)
    with pytest.raises(ContractError, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        again.reserve("claude")


def test_the_service_defaults_cover_real_rooms():
    # A whole room's onboarding and a 4-person negotiation need far more than the
    # one-off run's 4 calls; one job is limited separately by the database.
    config = ServeConfig()
    assert config.limits.model_calls >= 200 and config.limits.actor_runs >= 20
    assert config.job_model_calls >= 50


def test_service_actor_budget_is_valid_and_survives_config_reload():
    config = ServeConfig()
    assert config.limits.actor_runs == 40
    assert ServeConfig.model_validate_json(config.model_dump_json()) == config
    assert LiveLimits().actor_runs == 1
    assert LiveLimits(actor_runs=40).actor_runs == 40


@pytest.mark.parametrize("actor_runs", [0, 41])
def test_actor_budget_remains_bounded(actor_runs):
    with pytest.raises(ValidationError):
        LiveLimits(actor_runs=actor_runs)


def test_authorized_daily_override_preserves_usage_and_expires(tmp_path):
    today = ["2026-11-01"]
    budget = ProviderBudget(tmp_path / "budget.sqlite", "serve", model_calls=1,
                            actor_runs=2, daily=True, day=lambda: today[0])
    budget.reserve("apify")
    budget.reserve("apify")
    budget.set_daily_limit("apify", 3, utc_date=today[0])
    # An already-created budget sees the operator change on its next attempt.
    again = ProviderBudget(tmp_path / "budget.sqlite", "serve", model_calls=1,
                           actor_runs=2, daily=True, day=lambda: today[0])
    assert again.reserve("apify") == 3
    with pytest.raises(ContractError, match="ACTOR_RUN_BUDGET_EXHAUSTED"):
        budget.reserve("apify")
    assert budget.report()["limits"] == {"claude": 1, "apify": 3}
    assert budget.report()["used"]["apify"] == 3
    today[0] = "2026-11-02"
    assert budget.report()["limits"] == {"claude": 1, "apify": 2}
    assert budget.reserve("apify") == 1
    # Other service scopes and one-off runs cannot inherit the override.
    other = ProviderBudget(tmp_path / "budget.sqlite", "other", daily=True, day=lambda: "2026-11-01")
    assert other.report()["limits"]["apify"] == 1
    with pytest.raises(ValueError, match="INVALID_DAILY_LIMIT"):
        ProviderBudget(tmp_path / "budget.sqlite", "run").set_daily_limit("apify", 3, utc_date="2026-11-01")
