"""Explicit uncapped observation policy; synthetic prices and transports only."""
import pytest

from app_v2.coach_config import CoachConfigurationError, load_coach_config
from app_v2.services.coach_ai import CoachAIConfig, CoachAIConfigurationError, CoachAIError, ProviderResponse
from tests_v2.unit.test_coach_ai import config, gateway, MESSAGES


def observe_environment(**changes):
    return {
        'COACH_ENV': 'test', 'COACH_AI_ENABLED': '1', 'COACH_OPENAI_API_KEY': 'synthetic-key',
        'COACH_AI_MODEL': 'synthetic-model', 'COACH_AI_BUDGET_MODE': 'observe',
        'COACH_AI_INPUT_USD_PER_MILLION': '1', 'COACH_AI_OUTPUT_USD_PER_MILLION': '2',
        'COACH_AI_MAX_INPUT_TOKENS': '16384', 'COACH_AI_MAX_OUTPUT_TOKENS': '256',
        'COACH_AI_TIMEOUT_SECONDS': '10', **changes,
    }


def test_observe_mode_has_no_daily_cap_and_maps_to_gateway_policy():
    dedicated = load_coach_config(observe_environment())
    assert dedicated.ai_budget_mode == 'observe' and dedicated.daily_cap_microusd is None
    assert dedicated.ai_config() == config(budget_mode='observe', daily_cap_microusd=None)


@pytest.mark.parametrize('cap', ['1', '0', 'unlimited'])
def test_observe_mode_rejects_a_contradictory_daily_cap(cap):
    with pytest.raises(CoachConfigurationError, match='must be unset'):
        load_coach_config(observe_environment(COACH_AI_EMERGENCY_DAILY_USD=cap))


def test_capped_remains_default_and_requires_explicit_money():
    values = observe_environment()
    values.pop('COACH_AI_BUDGET_MODE')
    with pytest.raises(CoachConfigurationError, match='COACH_AI_EMERGENCY_DAILY_USD'):
        load_coach_config(values)
    values['COACH_AI_EMERGENCY_DAILY_USD'] = '1'
    assert load_coach_config(values).ai_config().budget_mode == 'capped'


@pytest.mark.parametrize('field', ['COACH_AI_MODEL', 'COACH_AI_INPUT_USD_PER_MILLION',
                                  'COACH_AI_OUTPUT_USD_PER_MILLION', 'COACH_AI_MAX_INPUT_TOKENS',
                                  'COACH_AI_MAX_OUTPUT_TOKENS'])
def test_observe_mode_still_requires_priced_bounded_requests(field):
    values = observe_environment()
    values.pop(field)
    with pytest.raises(CoachConfigurationError, match=field):
        load_coach_config(values)


def test_unknown_budget_policy_is_not_implicitly_uncapped():
    with pytest.raises(CoachConfigurationError, match='COACH_AI_BUDGET_MODE'):
        load_coach_config(observe_environment(COACH_AI_BUDGET_MODE='off'))
    with pytest.raises(CoachAIConfigurationError, match='budget_mode'):
        config(budget_mode='off')
    with pytest.raises(CoachAIConfigurationError, match='requires daily_cap_microusd=None'):
        config(budget_mode='observe')
    with pytest.raises(CoachAIConfigurationError, match='capped mode'):
        config(daily_cap_microusd=None)


def test_compatibility_ai_environment_accepts_only_explicit_observe_without_cap():
    env = {
        'NENOY_V2_COACH_AI_MODEL': 'synthetic-model', 'NENOY_V2_COACH_AI_BUDGET_MODE': 'observe',
        'NENOY_V2_COACH_AI_MAX_INPUT_TOKENS': '16384', 'NENOY_V2_COACH_AI_MAX_OUTPUT_TOKENS': '256',
        'NENOY_V2_COACH_AI_INPUT_PRICE_MICROUSD_PER_MILLION': '1000000',
        'NENOY_V2_COACH_AI_OUTPUT_PRICE_MICROUSD_PER_MILLION': '2000000',
        'NENOY_V2_COACH_AI_TIMEOUT_SECONDS': '10',
    }
    assert CoachAIConfig.from_env(env) == config(budget_mode='observe', daily_cap_microusd=None)
    env['NENOY_V2_COACH_AI_DAILY_CAP_MICROUSD'] = '100'
    with pytest.raises(CoachAIConfigurationError):
        CoachAIConfig.from_env(env)


def test_observe_gateway_still_persists_reservation_before_provider_and_usage_afterwards():
    ai, budget, provider = gateway(budget_mode='observe', daily_cap_microusd=None)
    result = ai.request_json(operation_id='observe-success', purpose='assessment', messages=MESSAGES)
    assert result.cost_microusd == 140
    assert budget.reserved['budget_mode'] == 'observe' and budget.reserved['daily_cap_microusd'] is None
    assert budget.events[:2] == ['reserve_committed', 'provider_call']
    assert budget.events[-1][0] == 'reconcile' and len(provider.calls) == 1


def test_observe_cannot_call_provider_when_pre_call_accounting_is_unavailable():
    ai, budget, provider = gateway(budget_mode='observe', daily_cap_microusd=None)
    def unavailable(**kwargs):
        raise RuntimeError('PRIVATE_DATABASE')
    budget.reserve = unavailable
    with pytest.raises(CoachAIError, match='AI was not called'):
        ai.request_json(operation_id='observe-db-down', purpose='assessment', messages=MESSAGES)
    assert provider.calls == []


def test_observe_unknown_usage_stops_further_unmeasurable_spending():
    ai, budget, provider = gateway(ProviderResponse('{}', None, None, 'synthetic-model'),
                                   budget_mode='observe', daily_cap_microusd=None)
    with pytest.raises(CoachAIError, match='usage is unknown'):
        ai.request_json(operation_id='observe-no-usage', purpose='assessment', messages=MESSAGES)
    assert len(provider.calls) == 1
    assert budget.events[-1][2]['outcome'] == 'usage_missing'
    assert budget.events[-1][2]['block_day'] is True
