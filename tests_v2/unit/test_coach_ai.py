from __future__ import annotations

from dataclasses import replace
import json

import httpx
import pytest

from app_v2.services.coach_ai import (
    CoachAI, CoachAIConfig, CoachAIConfigurationError, CoachAIError,
    OpenAIResponsesProvider, ProviderResponse,
)
from app_v2.repositories.coach_budget_repo import (
    CoachBudgetConfigurationError, CoachBudgetExceeded, CoachBudgetRepository,
    CoachOperationAlreadyAttempted, token_cost_microusd,
)


def config(**changes):
    # Invented unit-test rates, not claims about any actual provider price.
    return replace(CoachAIConfig('synthetic-model', 100_000, 16_384, 256, 1_000_000, 2_000_000, 10), **changes)


MESSAGES = [{'role': 'user', 'content': 'Synthetic JSON request'}]


class FakeBudget:
    def __init__(self):
        self.events = []
        self.reserved = None

    def reserve(self, **kwargs):
        self.events.append('reserve_committed')
        self.reserved = kwargs

    def reconcile(self, operation_id, **kwargs):
        self.events.append(('reconcile', operation_id, kwargs))
        return token_cost_microusd(kwargs['input_tokens'], kwargs['output_tokens'], 1_000_000, 2_000_000)

    def mark_uncertain(self, operation_id, **kwargs):
        self.events.append(('uncertain', operation_id, kwargs))


class FakeProvider:
    def __init__(self, budget, response=None, error=None):
        self.budget, self.error = budget, error
        self.response = response or ProviderResponse('{"action":"reply"}', 100, 20, 'synthetic-model')
        self.calls = []

    def complete_json(self, **kwargs):
        assert self.budget.events[0] == 'reserve_committed'
        self.calls.append(kwargs)
        self.budget.events.append('provider_call')
        if self.error:
            raise self.error
        return self.response


def gateway(response=None, error=None, **config_changes):
    budget = FakeBudget()
    provider = FakeProvider(budget, response, error)
    return CoachAI(budget, provider, config(**config_changes)), budget, provider


def test_config_has_no_default_live_prices_or_cap():
    with pytest.raises(CoachAIConfigurationError, match='Missing explicit'):
        CoachAIConfig.from_env({})
    env = {
        'NENOY_V2_COACH_AI_MODEL': 'synthetic-model',
        'NENOY_V2_COACH_AI_DAILY_CAP_MICROUSD': '100000',
        'NENOY_V2_COACH_AI_MAX_INPUT_TOKENS': '16384',
        'NENOY_V2_COACH_AI_MAX_OUTPUT_TOKENS': '256',
        'NENOY_V2_COACH_AI_INPUT_PRICE_MICROUSD_PER_MILLION': '1000000',
        'NENOY_V2_COACH_AI_OUTPUT_PRICE_MICROUSD_PER_MILLION': '2000000',
        'NENOY_V2_COACH_AI_TIMEOUT_SECONDS': '10',
    }
    assert CoachAIConfig.from_env(env) == config()
    del env['NENOY_V2_COACH_AI_INPUT_PRICE_MICROUSD_PER_MILLION']
    with pytest.raises(CoachAIConfigurationError):
        CoachAIConfig.from_env(env)


def test_dedicated_coach_config_maps_explicit_usd_to_gateway_microusd():
    from app_v2.coach_config import load_coach_config
    dedicated = load_coach_config({
        'COACH_ENV': 'test', 'COACH_AI_ENABLED': '1', 'COACH_OPENAI_API_KEY': 'synthetic-key',
        'COACH_AI_MODEL': 'synthetic-model', 'COACH_AI_EMERGENCY_DAILY_USD': '5',
        'COACH_AI_INPUT_USD_PER_MILLION': '2', 'COACH_AI_OUTPUT_USD_PER_MILLION': '10',
        'COACH_AI_MAX_INPUT_TOKENS': '16384', 'COACH_AI_MAX_OUTPUT_TOKENS': '256',
        'COACH_AI_TIMEOUT_SECONDS': '20',
    })
    assert not dedicated.enabled
    assert dedicated.ai_config() == CoachAIConfig('synthetic-model', 5_000_000, 16_384, 256,
                                                  2_000_000, 10_000_000, 20)


@pytest.mark.parametrize('url', [None, '', '   '])
def test_budget_repository_never_falls_back_to_another_runtime_database(monkeypatch, url):
    monkeypatch.setenv('NENOY_V2_DATABASE_URL', 'postgresql://synthetic-legacy.invalid/do-not-connect')
    with pytest.raises(CoachBudgetConfigurationError, match='explicit isolated'):
        CoachBudgetRepository(url)


@pytest.mark.parametrize('changes', [
    {'daily_cap_microusd': 0}, {'input_price_microusd_per_million': 0},
    {'output_price_microusd_per_million': 2.5}, {'max_output_tokens': True},
    {'timeout_seconds': float('inf')}, {'timeout_seconds': float('nan')}, {'model': ''},
])
def test_invalid_configuration_fails_before_request(changes):
    with pytest.raises(CoachAIConfigurationError):
        config(**changes)


def test_integer_monetary_rounding_is_upward_not_float():
    assert token_cost_microusd(1, 1, 1, 1) == 2
    assert token_cost_microusd(999_999, 1, 1_000_001, 1) == 1_000_001


def test_success_reserves_before_provider_then_reconciles():
    ai, budget, provider = gateway()
    result = ai.request_json(operation_id='synthetic:assessment:1', purpose='assessment', messages=MESSAGES)
    assert result.parsed == {'action': 'reply'}
    assert result.cost_microusd == 140
    assert budget.events[:2] == ['reserve_committed', 'provider_call']
    assert budget.events[2][0] == 'reconcile'
    assert budget.reserved['max_input_tokens'] == config().max_input_tokens
    assert provider.calls[0]['max_output_tokens'] == 256
    assert provider.calls[0]['timeout_seconds'] == 10
    assert len(budget.reserved['request_hash']) == 64


@pytest.mark.parametrize('purpose', ['assessment', 'indexing', 'summarization', 'intervention', 'personal_track', 'retry'])
def test_every_purpose_uses_the_same_reservation_gate(purpose):
    ai, budget, provider = gateway()
    ai.request_json(operation_id='synthetic:' + purpose, purpose=purpose, messages=MESSAGES)
    assert budget.reserved['purpose'] == purpose
    assert len(provider.calls) == 1


@pytest.mark.parametrize('error', [CoachBudgetExceeded('cap'), CoachOperationAlreadyAttempted('duplicate')])
def test_failed_budget_gate_never_calls_provider(error):
    ai, budget, provider = gateway()
    def denied(**kwargs):
        raise error
    budget.reserve = denied
    with pytest.raises(type(error)):
        ai.request_json(operation_id='denied', purpose='assessment', messages=MESSAGES)
    assert provider.calls == []


def test_database_error_is_sanitized_before_provider():
    ai, budget, provider = gateway()
    def unavailable(**kwargs):
        raise RuntimeError('PRIVATE_DSN_AND_PASSWORD')
    budget.reserve = unavailable
    with pytest.raises(CoachAIError) as error:
        ai.request_json(operation_id='db-down', purpose='assessment', messages=MESSAGES)
    assert 'PRIVATE_' not in str(error.value)
    assert provider.calls == []


def test_read_only_estimate_does_not_reserve_or_call():
    ai, budget, provider = gateway()
    estimate = ai.estimate_request(messages=MESSAGES)
    assert estimate['max_cost_microusd'] == config().max_cost_microusd
    assert 4096 < estimate['input_upper_bound'] <= config().max_input_tokens
    assert budget.events == [] and provider.calls == []


@pytest.mark.parametrize('messages,schema', [
    ([{'role': 'user', 'content': 'я' * 100_000}], None),
    ([{'role': 'user', 'content': [{'type': 'image_url', 'url': 'x'}]}], None),
    ([{'role': 'user', 'content': 'x', 'file_id': 'private-file'}], None),
    (MESSAGES, {'type': 'object', 'description': 'x' * 100_000}),
    (MESSAGES, {'type': 'array'}),
])
def test_oversized_or_unsupported_inputs_are_rejected_before_reservation(messages, schema):
    ai, budget, provider = gateway()
    with pytest.raises(CoachAIConfigurationError):
        ai.request_json(operation_id='bad-input', purpose='assessment', messages=messages, schema=schema)
    assert budget.events == [] and provider.calls == []


def test_silence_still_records_cost():
    ai, budget, provider = gateway(ProviderResponse('{"action":"SILENCE"}', 100, 20, 'synthetic-model'))
    result = ai.request_json(operation_id='silence', purpose='assessment', messages=MESSAGES)
    assert result.cost_microusd == 140
    assert budget.events[-1][2]['outcome'] == 'silence'


@pytest.mark.parametrize('text', ['not JSON', '[]', '{"x":NaN}', '{"x":1,"x":2}', ''])
def test_invalid_json_still_reconciles_known_usage(text):
    ai, budget, provider = gateway(ProviderResponse(text, 100, 20, 'synthetic-model'))
    with pytest.raises(CoachAIError, match='usage was recorded'):
        ai.request_json(operation_id='invalid-json', purpose='assessment', messages=MESSAGES)
    assert budget.events[-1][0] == 'reconcile'
    assert budget.events[-1][2]['outcome'] == 'invalid_json'
    assert len(provider.calls) == 1


def test_incomplete_reasoning_response_still_records_output_usage():
    ai, budget, provider = gateway(ProviderResponse('', 100, 256, 'synthetic-model', status='incomplete'))
    with pytest.raises(CoachAIError):
        ai.request_json(operation_id='incomplete', purpose='assessment', messages=MESSAGES)
    assert budget.events[-1][2]['output_tokens'] == 256
    assert budget.events[-1][2]['outcome'] == 'incomplete'


@pytest.mark.parametrize('error', [TimeoutError('PRIVATE_PROMPT'), RuntimeError('PRIVATE_API_KEY')])
def test_unknown_provider_failure_holds_reservation_without_retry_or_sensitive_error(error):
    ai, budget, provider = gateway(error=error)
    with pytest.raises(CoachAIError) as caught:
        ai.request_json(operation_id='uncertain', purpose='assessment', messages=MESSAGES)
    assert len(provider.calls) == 1
    assert budget.events[-1][0] == 'uncertain'
    assert not any(isinstance(row, tuple) and row[0] == 'reconcile' for row in budget.events)
    assert 'PRIVATE_' not in str(caught.value)


def test_missing_usage_holds_full_reservation():
    ai, budget, provider = gateway(ProviderResponse('{"action":"silence"}', None, None, 'synthetic-model'))
    with pytest.raises(CoachAIError, match='usage is unknown'):
        ai.request_json(operation_id='missing-usage', purpose='assessment', messages=MESSAGES)
    assert budget.events[-1][2]['outcome'] == 'usage_missing'


def test_unpriced_response_model_blocks_day_and_holds_reservation():
    ai, budget, provider = gateway(ProviderResponse('{}', 100, 20, 'unpriced-model'))
    with pytest.raises(CoachAIError, match='unpriced'):
        ai.request_json(operation_id='unknown-model', purpose='assessment', messages=MESSAGES)
    assert budget.events[-1][2]['block_day'] is True


def test_usage_beyond_configured_bound_is_recorded_and_rejected():
    ai, budget, provider = gateway(ProviderResponse('{}', 100, 257, 'synthetic-model'))
    with pytest.raises(CoachAIError, match='exceeded'):
        ai.request_json(operation_id='overrun', purpose='assessment', messages=MESSAGES)
    assert budget.events[-1][0] == 'reconcile'
    assert budget.events[-1][2]['output_tokens'] == 257


def test_telemetry_failure_after_provider_cannot_trigger_refund_or_second_call():
    ai, budget, provider = gateway()
    def unavailable(*args, **kwargs):
        raise RuntimeError('PRIVATE_DATABASE')
    budget.reconcile = unavailable
    with pytest.raises(CoachAIError, match='reconciliation unavailable'):
        ai.request_json(operation_id='settlement-db-down', purpose='assessment', messages=MESSAGES)
    assert len(provider.calls) == 1 and budget.reserved is not None


def test_responses_transport_has_bounded_one_post_without_store_tools_or_retries():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'model': 'synthetic-model', 'status': 'completed',
            'usage': {'input_tokens': 100, 'output_tokens': 20},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"action":"silence"}'}]}]})
    provider = OpenAIResponsesProvider('synthetic-key', transport=httpx.MockTransport(handler))
    response = provider.complete_json(model='synthetic-model', messages=MESSAGES, schema={'type': 'object'},
                                      max_output_tokens=256, timeout_seconds=10)
    assert len(seen) == 1 and seen[0].method == 'POST'
    assert str(seen[0].url) == 'https://api.openai.com/v1/responses'
    payload = json.loads(seen[0].content)
    assert payload['max_output_tokens'] == 256
    assert payload['store'] is payload['background'] is payload['stream'] is False
    assert payload['tools'] == [] and payload['truncation'] == 'disabled'
    assert 'previous_response_id' not in payload
    assert response.output_tokens == 20


def test_responses_transport_does_not_retry_http_or_timeout_errors():
    seen = []
    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout('synthetic timeout')
    provider = OpenAIResponsesProvider('synthetic-key', transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        provider.complete_json(model='synthetic-model', messages=MESSAGES, schema=None,
                               max_output_tokens=256, timeout_seconds=10)
    assert len(seen) == 1


def test_public_web_research_reserves_external_tool_cost_and_reconciles_usage():
    response = ProviderResponse('{"answer":"Verified public item","verified":true}', 100, 20,
        'synthetic-model', web_search_calls=2, web_sources=('https://example.com/item',))
    ai, budget, provider = gateway(response)
    result = ai.request_json(operation_id='web-research-1', purpose='web_research',
        messages=MESSAGES, schema={'type':'object'}, web_search=True)
    assert result.web_search_calls == 2
    assert result.web_sources == ('https://example.com/item',)
    assert budget.reserved['max_tool_cost_microusd'] == 40_000
    assert provider.calls[0]['web_search'] is True
    assert budget.events[-1][2]['tool_calls'] == 2
    assert budget.events[-1][2]['tool_cost_microusd'] == 20_000


def test_web_research_cannot_claim_success_when_provider_did_not_search():
    response = ProviderResponse('{"answer":"I think so","verified":true}', 100, 20, 'synthetic-model')
    ai, budget, provider = gateway(response)
    with pytest.raises(CoachAIError, match='required web search did not execute'):
        ai.request_json(operation_id='web-no-tool', purpose='web_research', messages=MESSAGES,
                        schema={'type':'object'}, web_search=True)
    assert len(provider.calls) == 1
    assert budget.events[-1][0] == 'reconcile'


def test_web_responses_tool_search_and_provider_verified_urls():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'synthetic-model', 'status': 'completed',
            'usage': {'input_tokens': 200, 'output_tokens': 60}, 'output': [
                {'type': 'web_search_call', 'status': 'completed',
                 'action': {'type': 'search', 'sources': [
                    {'url':'https://example.com/public','title':'Public official'},
                    {'url':'http://127.0.0.1/private','title':'Unsafe'},
                    {'url':'https://example.com/public','title':'Duplicate'}]}},
                {'type':'message','content':[{'type':'output_text',
                    'text':'{"answer":"Public source confirmed","verified":true}',
                    'annotations':[{'type':'url_citation','url':'https://example.org/news'}]}]},
            ]})
    provider = OpenAIResponsesProvider('synthetic-key', transport=httpx.MockTransport(handler))
    result = provider.complete_json(model='synthetic-model', messages=MESSAGES,
        schema={'type':'object'}, max_output_tokens=256, timeout_seconds=10, web_search=True)
    assert len(seen) == 1
    assert seen[0]['tools'] == [{'type':'web_search','search_context_size':'low'}]
    assert seen[0]['tool_choice'] == 'required'
    assert 'web_search_call.action.sources' in seen[0]['include']
    assert seen[0]['store'] is False
    assert result.web_search_calls == 1
    assert result.web_sources == ('https://example.com/public','https://example.org/news')
