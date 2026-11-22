from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
import uuid

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.repositories.coach_budget_repo import (
    CoachBudgetConfigurationError, CoachBudgetExceeded, CoachBudgetRepository,
    CoachOperationAlreadyAttempted,
)
from app_v2.services.coach_ai import CoachAI, CoachAIConfig, CoachAIError, ProviderResponse


AT = datetime(2026, 11, 22, 23, 30, tzinfo=timezone.utc)


@pytest.fixture
def budget():
    url = os.getenv('NENOY_V2_TEST_DATABASE_URL')
    if not url:
        pytest.skip('NENOY_V2_TEST_DATABASE_URL is not configured')
    psycopg = pytest.importorskip('psycopg')
    run_migrations(url)
    key = 'synthetic-budget-' + uuid.uuid4().hex
    repo = CoachBudgetRepository(url, budget_key=key)
    repo.test_url = url
    yield repo
    with psycopg.connect(url) as conn:
        conn.execute('DELETE FROM coach_ai_operations WHERE budget_key=%s', (key,))
        conn.execute('DELETE FROM coach_budget_days WHERE budget_key=%s', (key,))


def reserve(budget, operation='one', *, cap=120, now=AT, **changes):
    values = dict(operation_id=budget.budget_key + ':' + operation, purpose='assessment',
                  model='synthetic-model', request_hash='a' * 64, daily_cap_microusd=cap,
                  max_input_tokens=20, max_output_tokens=20,
                  input_price_microusd_per_million=1_000_000, output_price_microusd_per_million=2_000_000,
                  now=now)
    values.update(changes)
    return budget.reserve(**values)


def test_parallel_workers_share_one_monetary_cap_across_purposes(budget):
    barrier = Barrier(8)
    def worker(index):
        barrier.wait(timeout=5)
        try:
            reserve(budget, str(index), purpose=('assessment', 'personal_track')[index % 2])
            return 'reserved'
        except CoachBudgetExceeded:
            return 'denied'
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(worker, range(8)))
    assert outcomes.count('reserved') == 2
    assert outcomes.count('denied') == 6
    snapshot = budget.snapshot(now=AT)
    assert snapshot['reserved_microusd'] == snapshot['cap_microusd'] == 120
    assert snapshot['spent_microusd'] == snapshot['remaining_microusd'] == 0


@pytest.mark.parametrize('cap', [60, 120])
def test_parallel_duplicate_operation_can_reserve_only_once(budget, cap):
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=5)
        try:
            return reserve(budget, cap=cap).operation_id
        except CoachOperationAlreadyAttempted:
            return 'duplicate'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count('duplicate') == 1
    assert budget.snapshot(now=AT)['reserved_microusd'] == 60


def test_known_usage_releases_only_unused_reservation_and_reconciliation_is_idempotent(budget):
    reservation = reserve(budget)
    assert budget.reconcile(reservation.operation_id, input_tokens=10, output_tokens=5, outcome='silence') == 20
    assert budget.reconcile(reservation.operation_id, input_tokens=10, output_tokens=5, outcome='silence') == 20
    snapshot = budget.snapshot(now=AT)
    assert snapshot['reserved_microusd'] == 0 and snapshot['spent_microusd'] == 20
    assert snapshot['remaining_microusd'] == 100
    assert budget.operation(reservation.operation_id)['outcome'] == 'silence'
    with pytest.raises(CoachBudgetConfigurationError):
        budget.reconcile(reservation.operation_id, input_tokens=11, output_tokens=5, outcome='valid_json')


def test_timeout_and_crash_reservations_are_durable_and_never_expire_automatically(budget):
    reservation = reserve(budget)
    budget.mark_uncertain(reservation.operation_id)
    restarted = CoachBudgetRepository(budget.test_url, budget_key=budget.budget_key)
    assert restarted.operation(reservation.operation_id)['status'] == 'uncertain'
    assert restarted.snapshot(now=AT)['reserved_microusd'] == 60
    with pytest.raises(CoachOperationAlreadyAttempted):
        reserve(restarted, now=AT + timedelta(days=1))
    assert restarted.snapshot(now=AT)['reserved_microusd'] == 60


def test_retries_are_new_operations_and_need_new_financial_reservations(budget):
    first = reserve(budget, 'attempt-1')
    budget.mark_uncertain(first.operation_id)
    second = reserve(budget, 'attempt-2')
    assert first.operation_id != second.operation_id
    with pytest.raises(CoachBudgetExceeded):
        reserve(budget, 'attempt-3')
    assert budget.snapshot(now=AT)['reserved_microusd'] == 120


def test_utc_day_is_independent_of_worker_local_timezone(budget):
    first = reserve(budget, 'utc', cap=60)
    local = AT.astimezone(timezone(timedelta(hours=3)))
    assert local.date() != AT.date()
    with pytest.raises(CoachBudgetExceeded):
        reserve(budget, 'same-utc-day', cap=60, now=local)
    second = reserve(budget, 'next-day', cap=60, now=AT + timedelta(days=1))
    assert second.budget_day == first.budget_day + timedelta(days=1)


def test_config_mismatch_cannot_raise_cap_in_another_worker(budget):
    reserve(budget)
    with pytest.raises(CoachBudgetConfigurationError, match='disagree'):
        reserve(budget, 'different-worker', cap=1000)
    assert budget.snapshot(now=AT)['cap_microusd'] == 120


def test_usage_overrun_is_accounted_and_trips_breaker(budget):
    reservation = reserve(budget)
    cost = budget.reconcile(reservation.operation_id, input_tokens=100, output_tokens=20, outcome='valid_json')
    assert cost == 140
    snapshot = budget.snapshot(now=AT)
    assert snapshot['reserved_microusd'] == 0 and snapshot['spent_microusd'] == 140
    assert snapshot['blocked'] is True
    assert budget.operation(reservation.operation_id)['outcome'] == 'usage_overrun'
    with pytest.raises(CoachBudgetExceeded):
        reserve(budget, 'after-overrun')


def test_unknown_pricing_keeps_reservation_and_blocks_day(budget):
    reservation = reserve(budget)
    budget.mark_uncertain(reservation.operation_id, outcome='unknown_model', block_day=True)
    snapshot = budget.snapshot(now=AT)
    assert snapshot['reserved_microusd'] == 60 and snapshot['spent_microusd'] == 0 and snapshot['blocked']
    with pytest.raises(CoachBudgetExceeded):
        reserve(budget, 'after-unknown')


def test_no_reply_count_quota_small_useful_calls_fit_monetary_budget(budget):
    for index in range(10):
        reservation = reserve(budget, str(index), cap=600)
        budget.reconcile(reservation.operation_id, input_tokens=1, output_tokens=1, outcome='valid_json')
    assert budget.snapshot(now=AT)['spent_microusd'] == 30
    assert budget.snapshot(now=AT)['remaining_microusd'] == 570


class ObservingProvider:
    def __init__(self, budget, operation_id, *, text='{"action":"silence"}', error=None):
        self.budget, self.operation_id, self.text, self.error = budget, operation_id, text, error
        self.calls = 0

    def complete_json(self, **kwargs):
        # This read uses another database connection: the financial reservation
        # must already be COMMITTED before the network boundary is entered.
        assert self.budget.operation(self.operation_id)['status'] == 'reserved'
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResponse(self.text, 100, 20, kwargs['model'])


def configured_ai(budget, operation_id, **provider_options):
    cfg = CoachAIConfig('synthetic-model', 100_000, 16_384, 256, 1_000_000, 2_000_000, 10)
    provider = ObservingProvider(budget, operation_id, **provider_options)
    return CoachAI(budget, provider, cfg), provider, cfg


@pytest.mark.parametrize('text,outcome', [('{"action":"silence"}', 'silence'), ('invalid JSON', 'invalid_json')])
def test_gateway_commits_before_call_and_records_silence_or_invalid_json_cost(budget, text, outcome):
    operation = budget.budget_key + ':gateway'
    ai, provider, cfg = configured_ai(budget, operation, text=text)
    if outcome == 'invalid_json':
        with pytest.raises(CoachAIError):
            ai.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'SYNTHETIC_PRIVATE_TEXT'}])
    else:
        ai.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'SYNTHETIC_PRIVATE_TEXT'}])
    record = budget.operation(operation)
    assert record['status'] == 'settled' and record['outcome'] == outcome
    assert record['charged_microusd'] == 140
    assert 'SYNTHETIC_PRIVATE_TEXT' not in repr(record)
    assert provider.calls == 1
    with pytest.raises(CoachOperationAlreadyAttempted):
        ai.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'same or changed'}])
    assert provider.calls == 1


def test_gateway_uncertain_timeout_does_not_retry_or_refund(budget):
    operation = budget.budget_key + ':timeout'
    ai, provider, cfg = configured_ai(budget, operation, error=TimeoutError('SYNTHETIC_SECRET'))
    with pytest.raises(CoachAIError):
        ai.request_json(operation_id=operation, purpose='personal_track', messages=[{'role': 'user', 'content': 'Synthetic request'}])
    assert budget.operation(operation)['status'] == 'uncertain'
    assert budget.snapshot()['reserved_microusd'] == cfg.max_cost_microusd
    with pytest.raises(CoachOperationAlreadyAttempted):
        ai.request_json(operation_id=operation, purpose='personal_track', messages=[{'role': 'user', 'content': 'Synthetic request'}])
    assert provider.calls == 1


def test_crash_after_reservation_does_not_refund_or_replay_on_restart(budget):
    operation = budget.budget_key + ':crash'
    ai, provider, cfg = configured_ai(budget, operation, error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        ai.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'Synthetic request'}])
    assert budget.operation(operation)['status'] == 'reserved'
    restarted = CoachAI(CoachBudgetRepository(budget.test_url, budget_key=budget.budget_key), provider, cfg)
    with pytest.raises(CoachOperationAlreadyAttempted):
        restarted.request_json(operation_id=operation, purpose='assessment', messages=[{'role': 'user', 'content': 'Synthetic request'}])
    assert provider.calls == 1 and budget.snapshot()['reserved_microusd'] == cfg.max_cost_microusd
