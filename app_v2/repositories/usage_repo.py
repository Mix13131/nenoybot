from __future__ import annotations

from app_v2.domain.usage import LLMUsageRecord


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value)


class UsageRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def record(self, usage: LLMUsageRecord) -> int:
        row = self.conn.execute(
            """
            INSERT INTO llm_usage(
                event_id, intervention_id, task_kind, model,
                input_tokens, output_tokens, cached_tokens,
                estimated_cost_usd, latency_ms, success, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                usage.event_id,
                _optional_int(usage.intervention_id),
                usage.task_kind,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cached_tokens,
                usage.estimated_cost_usd,
                usage.latency_ms,
                usage.success,
                usage.created_at,
            ),
        ).fetchone()
        self.conn.commit()
        return int(row[0])
