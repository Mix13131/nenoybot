"""Durable pre-call accounting shared by all coach workers.

Every mutation owns a separate connection and commits before returning. Do not
share a caller's conversation transaction: a rollback after an HTTP call must
never refund money that may already have been spent. Uncertain/crashed attempts
retain their entire reservation indefinitely; there is no automatic release.
Observe mode retains this accounting while omitting a monetary spending cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Callable

from app_v2.adapters.postgres import connect


class CoachBudgetExceeded(RuntimeError):
    pass


class CoachOperationAlreadyAttempted(RuntimeError):
    pass


class CoachBudgetConfigurationError(ValueError):
    pass


_MAX_BIGINT = (1 << 63) - 1
_OUTCOMES = frozenset({
    'valid_json', 'silence', 'invalid_json', 'incomplete', 'provider_error',
    'usage_missing', 'unknown_model', 'usage_overrun',
})


def _positive(value: int, name: str) -> int:
    if type(value) is not int or not 0 < value <= _MAX_BIGINT:
        raise CoachBudgetConfigurationError(f'{name} must be a positive integer')
    return value


def token_cost_microusd(input_tokens: int, output_tokens: int, input_price: int, output_price: int) -> int:
    """Round each token class upward; never perform floating-point accounting."""
    for tokens in (input_tokens, output_tokens):
        if type(tokens) is not int or not 0 <= tokens <= _MAX_BIGINT:
            raise CoachBudgetConfigurationError('Token usage must be a nonnegative integer')
    _positive(input_price, 'input_price')
    _positive(output_price, 'output_price')
    cost = ((input_tokens * input_price + 999_999) // 1_000_000
            + (output_tokens * output_price + 999_999) // 1_000_000)
    if cost > _MAX_BIGINT:
        raise CoachBudgetConfigurationError('Configured token cost exceeds the ledger range')
    return cost


@dataclass(frozen=True)
class BudgetReservation:
    operation_id: str
    budget_key: str
    budget_day: date
    reserved_microusd: int


class CoachBudgetRepository:
    def __init__(self, database_url: str | None = None, *, connection_factory: Callable | None = None,
                 budget_key: str = 'coach') -> None:
        if not isinstance(budget_key, str) or not 1 <= len(budget_key) <= 80:
            raise CoachBudgetConfigurationError('A bounded budget key is required')
        if connection_factory is not None and not callable(connection_factory):
            raise CoachBudgetConfigurationError('An explicit connection factory must be callable')
        if connection_factory is None and (not isinstance(database_url, str) or not database_url.strip()):
            raise CoachBudgetConfigurationError('An explicit isolated budget database is required')
        self._connect = connection_factory if connection_factory is not None else (
            lambda: connect(database_url, connect_timeout=5))
        self.budget_key = budget_key

    @staticmethod
    def _day(conn, now: datetime | None = None) -> date:
        if now is None:
            return conn.execute("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date").fetchone()[0]
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise CoachBudgetConfigurationError('The test clock must be timezone-aware')
        return now.astimezone(timezone.utc).date()

    def reserve(self, *, operation_id: str, purpose: str, model: str, request_hash: str,
                daily_cap_microusd: int | None, max_input_tokens: int, max_output_tokens: int,
                input_price_microusd_per_million: int, output_price_microusd_per_million: int,
                budget_mode: str = 'capped',
                now: datetime | None = None) -> BudgetReservation:
        for value, name, limit in ((operation_id, 'operation_id', 200), (purpose, 'purpose', 80), (model, 'model', 160)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise CoachBudgetConfigurationError(f'A bounded {name} is required')
        if not isinstance(request_hash, str) or re.fullmatch('[0-9a-f]{64}', request_hash) is None:
            raise CoachBudgetConfigurationError('A SHA-256 request hash is required')
        if not isinstance(budget_mode, str) or budget_mode not in {'capped', 'observe'}:
            raise CoachBudgetConfigurationError('budget_mode must be capped or observe')
        if budget_mode == 'observe' and daily_cap_microusd is not None:
            raise CoachBudgetConfigurationError('observe mode requires daily_cap_microusd=None')
        cap = _positive(daily_cap_microusd, 'daily_cap_microusd') if budget_mode == 'capped' else None
        for value in (max_input_tokens, max_output_tokens):
            if not 0 < _positive(value, 'token bound') <= (1 << 31) - 1:
                raise CoachBudgetConfigurationError('Token bounds exceed the ledger range')
        reserved = token_cost_microusd(max_input_tokens, max_output_tokens,
                                      input_price_microusd_per_million, output_price_microusd_per_million)
        with self._connect() as conn:
            with conn.transaction():
                # Global operation identity includes attempts from prior days.
                if conn.execute('SELECT 1 FROM coach_ai_operations WHERE operation_id=%s', (operation_id,)).fetchone():
                    raise CoachOperationAlreadyAttempted('This operation was already reserved; it must not call AI again')
                day = self._day(conn, now)
                conn.execute(
                    '''INSERT INTO coach_budget_days(budget_key,budget_day,cap_microusd,budget_mode)
                       VALUES (%s,%s,%s,%s) ON CONFLICT (budget_key,budget_day) DO NOTHING''',
                    (self.budget_key, day, cap, budget_mode),
                )
                row = conn.execute(
                    '''SELECT cap_microusd,reserved_microusd,spent_microusd,blocked,budget_mode
                       FROM coach_budget_days WHERE budget_key=%s AND budget_day=%s FOR UPDATE''',
                    (self.budget_key, day),
                ).fetchone()
                # A competing worker may have committed this operation while
                # we waited for the day lock. Identity wins over a now-full cap.
                if conn.execute('SELECT 1 FROM coach_ai_operations WHERE operation_id=%s', (operation_id,)).fetchone():
                    raise CoachOperationAlreadyAttempted('This operation was already reserved; it must not call AI again')
                if (row[0], row[4]) != (cap, budget_mode):
                    raise CoachBudgetConfigurationError('Workers disagree about the daily accounting policy')
                if row[3]:
                    raise CoachBudgetExceeded('The AI accounting safety breaker is blocked')
                if cap is not None and row[1] + row[2] + reserved > cap:
                    raise CoachBudgetExceeded('The daily monetary safety budget is unavailable')
                if row[1] + reserved > _MAX_BIGINT:
                    raise CoachBudgetConfigurationError('Reserved accounting amount exceeds the ledger range')
                inserted = conn.execute(
                    '''INSERT INTO coach_ai_operations(
                           operation_id,budget_key,budget_day,purpose,model,request_hash,
                           max_input_tokens,max_output_tokens,input_price_microusd_per_million,
                           output_price_microusd_per_million,reserved_microusd,status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved')
                       ON CONFLICT (operation_id) DO NOTHING RETURNING operation_id''',
                    (operation_id, self.budget_key, day, purpose, model, request_hash,
                     max_input_tokens, max_output_tokens, input_price_microusd_per_million,
                     output_price_microusd_per_million, reserved),
                ).fetchone()
                if inserted is None:
                    raise CoachOperationAlreadyAttempted('This operation was already reserved; it must not call AI again')
                conn.execute(
                    '''UPDATE coach_budget_days SET reserved_microusd=reserved_microusd+%s
                       WHERE budget_key=%s AND budget_day=%s''', (reserved, self.budget_key, day),
                )
        # The connection context has committed before transport may start.
        return BudgetReservation(operation_id, self.budget_key, day, reserved)

    def reconcile(self, operation_id: str, *, input_tokens: int, output_tokens: int,
                  outcome: str, latency_ms: int = 0) -> int:
        if outcome not in _OUTCOMES:
            raise CoachBudgetConfigurationError('Unknown accounting outcome')
        if type(latency_ms) is not int or not 0 <= latency_ms <= (1 << 31) - 1:
            raise CoachBudgetConfigurationError('Latency must be a bounded nonnegative integer')
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    '''SELECT budget_day,reserved_microusd,input_price_microusd_per_million,
                              output_price_microusd_per_million,status,charged_microusd,input_tokens,
                              output_tokens,max_input_tokens,max_output_tokens
                       FROM coach_ai_operations WHERE operation_id=%s AND budget_key=%s FOR UPDATE''',
                    (operation_id, self.budget_key),
                ).fetchone()
                if row is None:
                    raise CoachBudgetConfigurationError('Cannot reconcile an unreserved operation')
                cost = token_cost_microusd(input_tokens, output_tokens, row[2], row[3])
                if row[4] == 'settled':
                    if (row[5], row[6], row[7]) != (cost, input_tokens, output_tokens):
                        raise CoachBudgetConfigurationError('A settled operation has conflicting usage')
                    return cost
                overrun = input_tokens > row[8] or output_tokens > row[9] or cost > row[1]
                day = conn.execute(
                    '''SELECT reserved_microusd,spent_microusd FROM coach_budget_days
                       WHERE budget_key=%s AND budget_day=%s FOR UPDATE''', (self.budget_key, row[0]),
                ).fetchone()
                if day[0] < row[1] or day[1] + cost > _MAX_BIGINT:
                    raise CoachBudgetConfigurationError('Budget accounting invariant failed')
                conn.execute(
                    '''UPDATE coach_budget_days
                       SET reserved_microusd=reserved_microusd-%s,spent_microusd=spent_microusd+%s,
                           blocked=blocked OR %s,
                           blocked_reason=CASE WHEN %s THEN 'usage_overrun' ELSE blocked_reason END
                       WHERE budget_key=%s AND budget_day=%s''',
                    (row[1], cost, overrun, overrun, self.budget_key, row[0]),
                )
                conn.execute(
                    '''UPDATE coach_ai_operations SET status='settled',charged_microusd=%s,
                           input_tokens=%s,output_tokens=%s,outcome=%s,latency_ms=%s,reconciled_at=CURRENT_TIMESTAMP
                       WHERE operation_id=%s''',
                    (cost, input_tokens, output_tokens, 'usage_overrun' if overrun else outcome, latency_ms, operation_id),
                )
        return cost

    def mark_uncertain(self, operation_id: str, *, outcome: str = 'provider_error',
                       block_day: bool = False, latency_ms: int = 0) -> None:
        if outcome not in _OUTCOMES or type(block_day) is not bool:
            raise CoachBudgetConfigurationError('Unknown accounting outcome')
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    '''SELECT budget_day,status FROM coach_ai_operations
                       WHERE operation_id=%s AND budget_key=%s FOR UPDATE''', (operation_id, self.budget_key),
                ).fetchone()
                if row is None:
                    raise CoachBudgetConfigurationError('Cannot mark an unreserved operation')
                if row[1] == 'settled':
                    return
                conn.execute(
                    "UPDATE coach_ai_operations SET status='uncertain',outcome=%s,latency_ms=%s WHERE operation_id=%s",
                    (outcome, max(0, min(int(latency_ms), (1 << 31) - 1)), operation_id),
                )
                if block_day:
                    conn.execute(
                        '''UPDATE coach_budget_days SET blocked=TRUE,blocked_reason=%s
                           WHERE budget_key=%s AND budget_day=%s''', (outcome, self.budget_key, row[0]),
                    )

    def snapshot(self, *, now: datetime | None = None) -> dict:
        with self._connect() as conn:
            day = self._day(conn, now)
            row = conn.execute(
                '''SELECT cap_microusd,reserved_microusd,spent_microusd,blocked,blocked_reason,budget_mode
                   FROM coach_budget_days WHERE budget_key=%s AND budget_day=%s''', (self.budget_key, day),
            ).fetchone()
        if row is None:
            return {'budget_day': day, 'configured': False}
        return dict(budget_day=day, configured=True, cap_microusd=row[0], reserved_microusd=row[1],
                    spent_microusd=row[2], remaining_microusd=None if row[0] is None else max(0, row[0] - row[1] - row[2]),
                    blocked=row[3], blocked_reason=row[4], budget_mode=row[5])

    def usage_stats(self, *, since: datetime | None = None, until: datetime | None = None) -> dict:
        """Read-only aggregate of attempts, without prompts, replies or identities.

        The half-open range selects attempt creation time in UTC. Costs use the
        explicit configured token tariff; uncertain/reserved exposure is shown
        separately and is never represented as confirmed spending or refunded.
        """
        for value in (since, until):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None
                                      or value.utcoffset() is None):
                raise CoachBudgetConfigurationError('Statistics boundaries must be timezone-aware')
        since = since.astimezone(timezone.utc) if since is not None else None
        until = until.astimezone(timezone.utc) if until is not None else None
        if since is not None and until is not None and since >= until:
            raise CoachBudgetConfigurationError('Statistics require a nonempty date range')
        conditions, params = ['o.budget_key=%s'], [self.budget_key]
        if since is not None:
            conditions.append('o.created_at>=%s')
            params.append(since)
        if until is not None:
            conditions.append('o.created_at<%s')
            params.append(until)
        with self._connect() as conn:
            rows = conn.execute(
                '''SELECT d.budget_mode,o.purpose,o.model,o.status,o.outcome,count(*),
                          COALESCE(sum(o.input_tokens),0),COALESCE(sum(o.output_tokens),0),
                          COALESCE(sum(o.charged_microusd),0),
                          COALESCE(sum(o.reserved_microusd) FILTER (WHERE o.status<>'settled'),0),
                          count(o.latency_ms),COALESCE(sum(o.latency_ms),0),max(o.latency_ms)
                   FROM coach_ai_operations o JOIN coach_budget_days d
                     ON d.budget_key=o.budget_key AND d.budget_day=o.budget_day
                   WHERE ''' + ' AND '.join(conditions) +
                ' GROUP BY d.budget_mode,o.purpose,o.model,o.status,o.outcome '
                'ORDER BY d.budget_mode,o.purpose,o.model,o.status,o.outcome', params,
            ).fetchall()
        metric_names = ('operations', 'input_tokens', 'output_tokens', 'settled_cost_microusd',
                        'unsettled_reserved_microusd', 'latency_samples', 'total_latency_ms')
        totals = dict.fromkeys(metric_names, 0)
        totals.update(settled_operations=0, reserved_operations=0, uncertain_operations=0,
                      maximum_latency_ms=None)
        breakdown = []
        for row in rows:
            entry = dict(zip(('budget_mode', 'purpose', 'model', 'status', 'outcome'), row[:5]))
            metrics = dict(zip(metric_names, (int(value) for value in row[5:12])))
            entry.update(metrics, maximum_latency_ms=row[12])
            breakdown.append(entry)
            for name, value in metrics.items():
                totals[name] += value
            totals[row[3] + '_operations'] += metrics['operations']
            if row[12] is not None:
                totals['maximum_latency_ms'] = max(totals['maximum_latency_ms'] or 0, row[12])
        average = (round(totals['total_latency_ms'] / totals['latency_samples'], 2)
                   if totals['latency_samples'] else None)
        return dict(budget_key=self.budget_key, since=since.isoformat() if since else None,
                    until=until.isoformat() if until else None,
                    cost_basis='configured_token_tariffs', average_latency_ms=average,
                    **totals, breakdown=breakdown)

    def operation(self, operation_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                '''SELECT status,outcome,reserved_microusd,charged_microusd,input_tokens,output_tokens,
                          request_hash,purpose,model,budget_day
                   FROM coach_ai_operations WHERE operation_id=%s AND budget_key=%s''', (operation_id, self.budget_key),
            ).fetchone()
        return dict(zip(('status', 'outcome', 'reserved_microusd', 'charged_microusd', 'input_tokens',
                         'output_tokens', 'request_hash', 'purpose', 'model', 'budget_day'), row)) if row else None
