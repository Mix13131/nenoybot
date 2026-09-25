import pytest

from app_v2.config import ConfigurationError, load_config


def test_development_config_requires_no_real_secrets() -> None:
    config = load_config({"NENOY_V2_ENV": "development"})

    assert config.environment == "development"
    assert config.webhook_secret is None
    assert config.database_url is None
    assert config.telegram_bot_token is None


def test_test_config_requires_no_real_secrets() -> None:
    config = load_config({"NENOY_V2_ENV": "test"})

    assert config.environment == "test"
    assert config.webhook_secret is None


def test_production_missing_runtime_credentials_fails_clearly() -> None:
    with pytest.raises(ConfigurationError, match="Production requires") as exc:
        load_config({"NENOY_V2_ENV": "production"})
    message = str(exc.value)
    assert "NENOY_V2_WEBHOOK_SECRET" in message
    assert "NENOY_V2_DATABASE_URL" in message
    assert "NENOY_V2_OPENAI_API_KEY" in message
    assert "NENOY_V2_TELEGRAM_BOT_TOKEN" in message


def test_production_requires_bot_identity_after_core_secrets() -> None:
    env = {
        "NENOY_V2_ENV": "production",
        "NENOY_V2_WEBHOOK_SECRET": "secret",
        "NENOY_V2_DATABASE_URL": "postgresql://db",
        "NENOY_V2_OPENAI_API_KEY": "sk-test",
        "NENOY_V2_TELEGRAM_BOT_TOKEN": "123:test",
    }
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_USERNAME or NENOY_V2_TELEGRAM_BOT_USER_ID"):
        load_config(env)


def test_production_with_complete_credentials_loads() -> None:
    config = load_config(
        {
            "NENOY_V2_ENV": "production",
            "NENOY_V2_WEBHOOK_SECRET": "test-secret",
            "NENOY_V2_DATABASE_URL": "postgresql://db",
            "NENOY_V2_OPENAI_API_KEY": "sk-test",
            "NENOY_V2_TELEGRAM_BOT_TOKEN": "123:test",
            "NENOY_V2_TELEGRAM_BOT_USERNAME": "@nenoy_v2_test",
        }
    )

    assert config.environment == "production"
    assert config.webhook_secret == "test-secret"
    assert config.database_url == "postgresql://db"
    assert config.telegram_bot_token == "123:test"
    assert config.telegram_bot_username == "nenoy_v2_test"


def test_invalid_environment_fails_clearly() -> None:
    with pytest.raises(ConfigurationError, match="Invalid NENOY_V2_ENV"):
        load_config({"NENOY_V2_ENV": "staging"})



def test_url_reader_defaults_are_bounded_and_enabled() -> None:
    config = load_config({"NENOY_V2_ENV": "development"})

    assert config.url_reader_enabled is True
    assert config.url_reader_timeout_seconds == 8.0
    assert config.url_reader_max_download_bytes == 5_000_000
    assert config.url_reader_max_content_chars == 30_000
    assert config.url_reader_cache_ttl_seconds == 86_400
    assert config.firecrawl_api_key is None


def test_url_reader_config_can_be_disabled_and_firecrawl_is_optional() -> None:
    config = load_config(
        {
            "NENOY_V2_ENV": "development",
            "NENOY_V2_URL_READER_ENABLED": "false",
            "NENOY_V2_URL_READER_TIMEOUT_SECONDS": "4.5",
            "NENOY_V2_URL_READER_MAX_DOWNLOAD_BYTES": "123456",
            "NENOY_V2_URL_READER_MAX_CONTENT_CHARS": "9876",
            "NENOY_V2_URL_READER_CACHE_TTL_SECONDS": "600",
            "NENOY_V2_FIRECRAWL_API_KEY": "fc-test",
            "NENOY_V2_FIRECRAWL_TIMEOUT_SECONDS": "12",
        }
    )

    assert config.url_reader_enabled is False
    assert config.url_reader_timeout_seconds == 4.5
    assert config.url_reader_max_download_bytes == 123456
    assert config.url_reader_max_content_chars == 9876
    assert config.url_reader_cache_ttl_seconds == 600
    assert config.firecrawl_api_key == "fc-test"
    assert config.firecrawl_timeout_seconds == 12.0


def test_invalid_url_reader_boolean_fails_clearly() -> None:
    with pytest.raises(ConfigurationError, match="NENOY_V2_URL_READER_ENABLED must be a boolean"):
        load_config(
            {
                "NENOY_V2_ENV": "development",
                "NENOY_V2_URL_READER_ENABLED": "sometimes",
            }
        )
