from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app_v2.domain.enums import ScopeType


@dataclass(frozen=True)
class DuplicateMemoryGroup:
    scope_type: ScopeType
    scope_id: str
    memory_type: str
    memory_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScopeMemoryCount:
    scope_type: ScopeType
    scope_id: str
    active_count: int


class MaintenanceRepository:
    """Bounded, explicit maintenance SQL for v2 durable stores."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def expire_raw_message_text(self, *, now: datetime, retention_days: int) -> int:
        rows = self.conn.execute(
            """
            UPDATE messages
            SET text = NULL,
                expires_at = COALESCE(expires_at, created_at + (%s * INTERVAL '1 day'))
            WHERE text IS NOT NULL
              AND COALESCE(expires_at, created_at + (%s * INTERVAL '1 day')) <= %s
            RETURNING id
            """,
            (retention_days, retention_days, now),
        ).fetchall()
        self.conn.commit()
        return len(rows)

    def decay_memory_freshness(self, *, now: datetime) -> int:
        """Apply an idempotent age ceiling; repeated runs at the same time do not compound."""
        rows = self.conn.execute(
            """
            UPDATE memory_cards
            SET freshness = LEAST(
                freshness,
                GREATEST(
                    0.05,
                    1.0 / (
                        1.0 + GREATEST(
                            0.0,
                            EXTRACT(EPOCH FROM (%s - COALESCE(last_confirmed_at, created_at))) / 86400.0
                        ) / 90.0
                    )
                )
            )
            WHERE status IN ('active', 'candidate')
              AND pinned IS FALSE
              AND COALESCE(last_confirmed_at, created_at) < %s
              AND freshness > GREATEST(
                    0.05,
                    1.0 / (
                        1.0 + GREATEST(
                            0.0,
                            EXTRACT(EPOCH FROM (%s - COALESCE(last_confirmed_at, created_at))) / 86400.0
                        ) / 90.0
                    )
              )
            RETURNING id
            """,
            (now, now, now),
        ).fetchall()
        self.conn.commit()
        return len(rows)

    def duplicate_memory_groups(self, *, limit_groups: int = 100) -> list[DuplicateMemoryGroup]:
        rows = self.conn.execute(
            """
            SELECT scope_type,
                   scope_id,
                   memory_type,
                   array_agg(
                       id ORDER BY
                           pinned DESC,
                           (status = 'active') DESC,
                           (importance * confidence * freshness) DESC,
                           source_count DESC,
                           updated_at DESC,
                           id
                   ) AS memory_ids
            FROM memory_cards
            WHERE status IN ('active', 'candidate')
            GROUP BY scope_type,
                     scope_id,
                     memory_type,
                     lower(regexp_replace(btrim(summary), '\\s+', ' ', 'g'))
            HAVING count(*) > 1
            ORDER BY count(*) DESC
            LIMIT %s
            """,
            (max(1, limit_groups),),
        ).fetchall()
        return [
            DuplicateMemoryGroup(
                scope_type=ScopeType(str(row[0])),
                scope_id=str(row[1]),
                memory_type=str(row[2]),
                memory_ids=tuple(str(item) for item in row[3]),
            )
            for row in rows
        ]

    def active_scope_counts(self) -> list[ScopeMemoryCount]:
        rows = self.conn.execute(
            """
            SELECT scope_type, scope_id, count(*)
            FROM memory_cards
            WHERE status='active'
            GROUP BY scope_type, scope_id
            """
        ).fetchall()
        return [
            ScopeMemoryCount(
                scope_type=ScopeType(str(row[0])),
                scope_id=str(row[1]),
                active_count=int(row[2]),
            )
            for row in rows
        ]

    def archive_low_quality_active(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        max_rows: int,
        now: datetime,
        unused_days: int,
        freshness_threshold: float,
        confidence_threshold: float,
    ) -> int:
        if max_rows <= 0:
            return 0
        rows = self.conn.execute(
            """
            WITH candidates AS (
                SELECT id
                FROM memory_cards
                WHERE scope_type=%s
                  AND scope_id=%s
                  AND status='active'
                  AND pinned IS FALSE
                  AND freshness <= %s
                  AND confidence < %s
                  AND COALESCE(last_used_at, last_confirmed_at, created_at)
                      <= %s - (%s * INTERVAL '1 day')
                ORDER BY freshness ASC, confidence ASC, importance ASC, updated_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE memory_cards AS mc
            SET status='archived', updated_at=%s
            FROM candidates
            WHERE mc.id=candidates.id
            RETURNING mc.id
            """,
            (
                scope_type.value,
                scope_id,
                freshness_threshold,
                confidence_threshold,
                now,
                unused_days,
                max_rows,
                now,
            ),
        ).fetchall()
        self.conn.commit()
        return len(rows)

    def archive_stale_running_jokes(
        self,
        *,
        now: datetime,
        unused_days: int,
        freshness_threshold: float,
    ) -> int:
        rows = self.conn.execute(
            """
            UPDATE memory_cards
            SET status='archived', updated_at=%s
            WHERE status='active'
              AND memory_type='running_joke'
              AND pinned IS FALSE
              AND freshness <= %s
              AND COALESCE(last_used_at, last_confirmed_at, created_at)
                  <= %s - (%s * INTERVAL '1 day')
            RETURNING id
            """,
            (now, freshness_threshold, now, unused_days),
        ).fetchall()
        self.conn.commit()
        return len(rows)

    def prune_terminal_events(self, *, now: datetime, retention_days: int) -> int:
        rows = self.conn.execute(
            """
            DELETE FROM events
            WHERE status IN ('completed', 'failed')
              AND COALESCE(processed_at, created_at) <= %s - (%s * INTERVAL '1 day')
            RETURNING id
            """,
            (now, retention_days),
        ).fetchall()
        self.conn.commit()
        return len(rows)

    def prune_terminal_outbox(self, *, now: datetime, retention_days: int) -> int:
        rows = self.conn.execute(
            """
            DELETE FROM outbox
            WHERE status IN ('sent', 'failed')
              AND COALESCE(sent_at, created_at) <= %s - (%s * INTERVAL '1 day')
            RETURNING id
            """,
            (now, retention_days),
        ).fetchall()
        self.conn.commit()
        return len(rows)
