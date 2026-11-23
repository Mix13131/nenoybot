"""Observe mode uses the real ledger with fake providers and invented tariffs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Barrier

import pytest

from app_v2.repositories.coach_budget_repo import (
    CoachBudgetConfigurationError, CoachBudgetExceeded, CoachBudgetRepository,
    CoachOperationAlreadyAttempted,
)
from app_v2.services.coach_ai import CoachAI, CoachAIConfig, CoachAIError
from tests_v2.integration.test_coach_budget_postgres import AT, ObservingProvider, budget, reserve


def observe_reserve(budget, operation='one', **changes):
    return reserve(budget, operation, cap=None, budget_mode='observe', **changes)


def observation_config():
    return CoachAIConfig('synthetic-model', None, 16_384, 256, 1_000_000, 2_000_000, 10,
                         budget_mode='observe')


def test_parallel_observation_does_not_apply_a_hidden_money_or_message_cap(budget):
    barrier = Barrier(8)
    def worker(index):
        other = CoachBudgetRepository(budget.test_url, budget_key=budget.budget_key)
        barrier.wait(timeout=5)
        return observe_reserve(other, str(index), purpose=('assessment', 'personal_track')[index % 2])
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(worker, range(8)))
    assert len({item.operation_id for item in reservations}) == 8
    snapshot = budget.snapshot(now=AT)
    assert snapshot['budget_mode'] == 'observe'
    assert snapshot['cap_microusd'] is None and snapshot['remaining_microusd'] is None
    assert snapshot['reserved_microusd'] == 480  # the equivalent capped fixture allows only 120
    assert snapshot['spent_microusd'] == 0 and snapshot['blocked'] is False


def test_parallel_duplicate_remains_single_durable_attempt_in_observe_mode(budget):
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=5)
        try:
            return observe_reserve(budget).operation_id
        except CoachOperationAlreadyAttempted:
            return 'duplicate'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: worker(), range(2)))
    assert result.count('duplicate') == 1
    assert budget.snapshot(now=AT)['reserved_microusd'] == 60


def test_observation_usage_and_uncertain_exposure_survive_restart_and_are_read_only(budget):
    first = observe_reserve(budget, 'settled', purpose='assessment')
    silent = observe_reserve(budget, 'silent', purpose='personal_track')
    uncertain = observe_reserve(budget, 'timeout', purpose='intervention')
    crashed = observe_reserve(budget, 'crashed', purpose='assessment')
    assert budget.reconcile(first.operation_id, input_tokens=10, output_tokens=5,
                            outcome='valid_json', latency_ms=12) == 20
    assert budget.reconcile(silent.operation_id, input_tokens=2, output_tokens=1,
                            outcome='silence', latency_ms=8) == 4
    budget.mark_uncertain(uncertain.operation_id, latency_ms=40)
    pg = pytest.importorskip('psycopg')
    readonly = CoachBudgetRepository(connection_factory=lambda: pg.connect(
        budget.test_url, options='-c default_transaction_read_only=on'), budget_key=budget.budget_key)
    stats = readonly.usage_stats()
    assert (stats['operations'], stats['settled_operations'], stats['uncertain_operations'],
            stats['reserved_operations']) == (4, 2, 1, 1)
    assert (stats['input_tokens'], stats['output_tokens']) == (12, 6)
    assert stats['settled_cost_microusd'] == 24 and stats['unsettled_reserved_microusd'] == 120
    assert stats['latency_samples'] == 3 and stats['average_latency_ms'] == 20
    assert stats['maximum_latency_ms'] == 40
    assert {row['outcome'] for row in stats['breakdown']} == {'valid_json', 'silence', 'provider_error', None}
    assert all(row['budget_mode'] == 'observe' for row in stats['breakdown'])
    assert not any(key in stats for key in ('text', 'prompt', 'response', 'operation_id', 'request_hash'))
    restarted = CoachBudgetRepository(budget.test_url, budget_key=budget.budget_key)
    assert restarted.snapshot(now=AT)['reserved_microusd'] == 120
    for operation in ('timeout', 'crashed'):
        with pytest.raises(CoachOperationAlreadyAttempted):
            observe_reserve(restarted, operation, now=AT + timedelta(days=1))
    assert readonly.usage_stats() == stats

    # Creation-time windows are half-open, including unsettled attempts.
    with pg.connect(budget.test_url) as conn:
        for index, reservation in enumerate((first, silent, uncertain, crashed)):
            conn.execute('UPDATE coach_ai_operations SET created_at=%s WHERE operation_id=%s',
                         (AT + timedelta(seconds=index), reservation.operation_id))
    window = readonly.usage_stats(since=AT + timedelta(seconds=1), until=AT + timedelta(seconds=3))
    assert window['operations'] == 2 and window['settled_cost_microusd'] == 4
    assert window['unsettled_reserved_microusd'] == 60 and window['uncertain_operations'] == 1


def test_empty_observation_statistics_create_no_ledger_records(budget):
    assert budget.usage_stats()['operations'] == 0
    assert budget.usage_stats()['average_latency_ms'] is None
    assert budget.snapshot(now=AT) == {'budget_day': AT.date(), 'configured': False}


def test_observe_gateway_records_cost_beyond_old_small_cap_without_repeating_operations(budget):
    cfg = observation_config()
    providers = []
    for index in range(3):
        operation = budget.budget_key + ':observe-gateway-' + str(index)
        provider = ObservingProvider(budget, operation)
        ai = CoachAI(budget, provider, cfg)
        result = ai.request_json(operation_id=operation, purpose='assessment',
                                 messages=[{'role': 'user', 'content': 'SYNTHETIC_PRIVATE_TEXT'}])
        assert result.cost_microusd == 140
        providers.append(provider)
    snapshot = budget.snapshot()
    assert snapshot['spent_microusd'] == 420 and snapshot['reserved_microusd'] == 0
    assert snapshot['cap_microusd'] is None and not snapshot['blocked']
    with pytest.raises(CoachOperationAlreadyAttempted):
        ai.request_json(operation_id=operation, purpose='assessment',
                        messages=[{'role': 'user', 'content': 'A changed payload is still a duplicate'}])
    assert all(provider.calls == 1 for provider in providers)
    stats = budget.usage_stats()
    assert stats['settled_cost_microusd'] == 420 and stats['input_tokens'] == 300
    assert stats['output_tokens'] == 60 and 'SYNTHETIC_PRIVATE_TEXT' not in repr(stats)


@pytest.mark.parametrize('signal', ['unknown_model', 'usage_missing', 'usage_overrun'])
def test_observe_keeps_provider_accounting_integrity_breakers(budget, signal):
    operation = budget.budget_key + ':integrity-' + signal
    provider = ObservingProvider(budget, operation)
    original = provider.complete_json
    def invalid_result(**kwargs):
        result = original(**kwargs)
        if signal == 'unknown_model':
            return replace(result, model='unpriced-model')
        if signal == 'usage_missing':
            return replace(result, input_tokens=None, output_tokens=None)
        return replace(result, output_tokens=observation_config().max_output_tokens + 1)
    provider.complete_json = invalid_result
    ai = CoachAI(budget, provider, observation_config())
    with pytest.raises(CoachAIError):
        ai.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'Synthetic'}])
    assert budget.snapshot()['blocked'] is True
    with pytest.raises(CoachBudgetExceeded):
        ai.request_json(operation_id=operation + ':next', purpose='assessment', messages=[{'role': 'user', 'content': 'Next'}])
    assert provider.calls == 1 and budget.operation(operation)['outcome'] == signal


@pytest.mark.parametrize('first_mode', ['capped', 'observe'])
def test_workers_cannot_silently_change_an_existing_daily_policy(budget, first_mode):
    if first_mode == 'capped':
        reserve(budget)
        conflicting = lambda: observe_reserve(budget, 'conflicting')
    else:
        observe_reserve(budget)
        conflicting = lambda: reserve(budget, 'conflicting')
    before = budget.snapshot(now=AT)
    with pytest.raises(CoachBudgetConfigurationError, match='disagree'):
        conflicting()
    assert budget.snapshot(now=AT) == before


def test_statistics_range_validation_cannot_issue_an_ambiguous_query(budget):
    with pytest.raises(CoachBudgetConfigurationError, match='timezone-aware'):
        budget.usage_stats(since=datetime(2026, 1, 1))
    with pytest.raises(CoachBudgetConfigurationError, match='nonempty'):
        budget.usage_stats(since=AT, until=AT)
