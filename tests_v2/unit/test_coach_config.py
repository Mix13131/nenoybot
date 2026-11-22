from dataclasses import replace

import pytest

from app_v2.coach_config import CoachConfigurationError, load_coach_config


def configured(**extra):
    return {
        "COACH_ENV": "test", "COACH_ENABLED": "1", "COACH_AI_ENABLED": "0",
        "COACH_DATABASE_URL": "postgresql://synthetic:secret@localhost/synthetic",
        "COACH_TELEGRAM_BOT_TOKEN": "900001:" + "s" * 30,
        "COACH_WEBHOOK_SECRET": "w" * 32, "COACH_BOT_USERNAME": "synthetic_coach_bot",
        "COACH_CHAT_ID": "-9000001", "COACH_TOPIC_IDS": "17,19",
        "COACH_ARCHIVE_POLICY": "until_author_deletes", **extra,
    }


def test_no_legacy_credentials_or_implicit_activation():
    config = load_coach_config({"NENOY_V2_DATABASE_URL": "legacy", "NENOY_V2_TELEGRAM_BOT_TOKEN": "legacy"})
    assert not config.enabled and not config.ai_enabled
    assert config.database_url is None and config.telegram_bot_token is None
    assert not config.permits_scope(-1, 17)


def test_explicit_identity_scope_and_sensitive_config_repr():
    config = load_coach_config(configured())
    assert config.bot_user_id == 900001
    assert config.permits_scope(-9000001, 17)
    assert not config.permits_scope(-9000002, 17)
    assert not config.permits_scope(-9000001, 18)
    assert "secret@" not in repr(config) and "ssssssssss" not in repr(config)
    assert "wwwwwwwwww" not in repr(config)


@pytest.mark.parametrize("change", [
    {"COACH_BOT_USER_ID": "900002"}, {"COACH_CHAT_ID": "123"},
    {"COACH_TOPIC_IDS": "0"}, {"COACH_TOPIC_IDS": "17,*"},
    {"COACH_TOPIC_IDS": "17,"}, {"COACH_ENABLED": "yes"},
    {"COACH_WEBHOOK_SECRET": "short"}, {"COACH_TELEGRAM_BOT_TOKEN": "secret"},
    {"COACH_ARCHIVE_POLICY": "unsupported"}, {"COACH_TRANSIENT_TTL_SECONDS": "90000"},
    {"COACH_PUBLIC_BASE_URL": "http://example.test"}, {"COACH_PUBLIC_BASE_URL": "https://secret@example.test"},
])
def test_unsafe_configuration_rejected(change):
    with pytest.raises(CoachConfigurationError):
        load_coach_config(configured(**change))


def test_ai_requires_explicit_financial_policy_even_with_key():
    with pytest.raises(CoachConfigurationError, match="COACH_AI_EMERGENCY_DAILY_USD"):
        load_coach_config(configured(COACH_AI_ENABLED="1", COACH_OPENAI_API_KEY="synthetic"))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "0.0000001", "1e99"])
def test_financial_amount_not_rounded_down_or_nonfinite(value):
    with pytest.raises(CoachConfigurationError):
        load_coach_config(configured(COACH_AI_EMERGENCY_DAILY_USD=value))


def test_exact_microusd_without_float():
    config = load_coach_config(configured(COACH_AI_EMERGENCY_DAILY_USD="1.234567"))
    assert config.daily_cap_microusd == 1234567


def test_configurable_human_identity():
    config = load_coach_config(configured(COACH_DISPLAY_NAME="Темп", COACH_ALIASES="Темп,Помощник,темп"))
    assert config.display_name == "Темп" and config.aliases == ("темп", "помощник")


def test_configured_bot_is_active_without_feature_switches_or_retention_confirmation():
    values = configured(
        COACH_OPENAI_API_KEY="synthetic-key", COACH_AI_MODEL="synthetic-model",
        COACH_AI_EMERGENCY_DAILY_USD="1", COACH_AI_INPUT_USD_PER_MILLION="1",
        COACH_AI_OUTPUT_USD_PER_MILLION="1", COACH_AI_MAX_INPUT_TOKENS="128000",
        COACH_AI_MAX_OUTPUT_TOKENS="4000",
    )
    for key in ("COACH_ENABLED", "COACH_AI_ENABLED", "COACH_ARCHIVE_POLICY"):
        values.pop(key)
    config = load_coach_config(values)
    assert config.enabled and config.ai_enabled
    assert config.archive_policy == "until_author_deletes"


def test_default_activation_still_requires_explicit_priced_ai_budget():
    values = configured()
    values.pop("COACH_ENABLED")
    values.pop("COACH_AI_ENABLED")
    with pytest.raises(CoachConfigurationError, match="COACH_AI_EMERGENCY_DAILY_USD"):
        load_coach_config(values)
