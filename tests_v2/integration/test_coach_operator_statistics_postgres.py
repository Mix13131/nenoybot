"""Real durable accounting for explicit operator diagnostics; synthetic provider."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app_v2.coach_admin import CoachPreparationError, inspect_statistics, prepare, smoke_ai
from app_v2.coach_config import CoachConfig
from app_v2.repositories.coach_budget_repo import CoachBudgetRepository
from app_v2.services.coach_ai import CoachAIError, ProviderResponse
from tests_v2.integration.test_coach_lifecycle_postgres import dedicated_database


@pytest.fixture
def observed(dedicated_database):
    pg, url = dedicated_database
    config = CoachConfig(
        environment="test", enabled=True, ai_enabled=True, group_mode="joined_groups",
        database_url=url, bot_user_id=909090002, ai_model="synthetic-model",
        ai_budget_mode="observe", daily_cap_microusd=None,
        input_price_microusd_per_million=2_500_000,
        output_price_microusd_per_million=15_000_000,
        max_input_tokens=272_000, max_output_tokens=16_000,
    )
    prepare(config)
    return config, pg


def test_synthetic_smoke_is_accounted_before_provider_and_stats_are_read_only(observed):
    config, pg = observed
    class Provider:
        def complete_json(self, **kwargs):
            # An independent connection observes the committed reservation.
            with pg.connect(config.database_url) as conn:
                assert conn.execute("SELECT status,purpose FROM coach_ai_operations").fetchall() == [
                    ("reserved", "operator_smoke")]
            assert kwargs["model"] == config.ai_model
            return ProviderResponse('{"ok":true}', 100, 10, config.ai_model)
    assert smoke_ai(config, provider=Provider()) == {
        "status": "ai_verified", "model": "synthetic-model", "input_tokens": 100,
        "output_tokens": 10, "cost_microusd": 400, "telegram_messages_sent": 0,
    }
    first = inspect_statistics(config)
    second = inspect_statistics(config)
    assert first == second
    assert first["today_utc"]["cap_microusd"] is None
    assert first["usage"]["operations"] == 1
    assert first["usage"]["settled_cost_microusd"] == 400
    assert first["usage"]["unsettled_reserved_microusd"] == 0
    assert first["runtime"]["groups"] == 0
    serialized = json.dumps(first, default=str)
    assert "Synthetic connection test" not in serialized
    assert config.database_url not in serialized
    assert "909090002" not in serialized
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert inspect_statistics(config, since=future)["usage"]["operations"] == 0


def test_failed_smoke_remains_uncertain_and_is_visible_in_stats(observed):
    config, _ = observed
    class Failing:
        def complete_json(self, **kwargs):
            raise TimeoutError("synthetic secret should never be returned")
    with pytest.raises(CoachAIError, match="Provider request failed"):
        smoke_ai(config, provider=Failing())
    usage = inspect_statistics(config)["usage"]
    assert usage["operations"] == usage["uncertain_operations"] == 1
    assert usage["settled_cost_microusd"] == 0
    assert usage["unsettled_reserved_microusd"] > 0


def test_unexpected_smoke_content_is_accounted_but_not_claimed_success(observed):
    config, _ = observed
    class Invalid:
        def complete_json(self, **kwargs):
            return ProviderResponse('{"ok":false}', 100, 10, config.ai_model)
    with pytest.raises(CoachPreparationError, match="failed_validation"):
        smoke_ai(config, provider=Invalid())
    assert CoachBudgetRepository(config.database_url).usage_stats()["settled_cost_microusd"] == 400


def test_disabled_ai_cannot_be_started_by_smoke(observed):
    config, _ = observed
    with pytest.raises(CoachPreparationError, match="not_enabled"):
        smoke_ai(replace(config, ai_enabled=False), provider=object())
    assert CoachBudgetRepository(config.database_url).usage_stats()["operations"] == 0
