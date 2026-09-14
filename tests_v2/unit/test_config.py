import pytest

from app_v2.config import ConfigurationError, load_config


def test_development_config_requires_no_real_secrets() -> None:
    config = load_config({"NENOY_V2_ENV": "development"})

    assert config.environment == "development"
    assert config.webhook_secret is None


def test_test_config_requires_no_real_secrets() -> None:
    config = load_config({"NENOY_V2_ENV": "test"})

    assert config.environment == "test"
    assert config.webhook_secret is None


def test_production_without_webhook_secret_fails_clearly() -> None:
    with pytest.raises(ConfigurationError, match="NENOY_V2_WEBHOOK_SECRET is required"):
        load_config({"NENOY_V2_ENV": "production"})


def test_production_with_webhook_secret_loads() -> None:
    config = load_config(
        {
            "NENOY_V2_ENV": "production",
            "NENOY_V2_WEBHOOK_SECRET": "test-secret",
        }
    )

    assert config.environment == "production"
    assert config.webhook_secret == "test-secret"


def test_invalid_environment_fails_clearly() -> None:
    with pytest.raises(ConfigurationError, match="Invalid NENOY_V2_ENV"):
        load_config({"NENOY_V2_ENV": "staging"})
