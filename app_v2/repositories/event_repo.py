from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    event_id: str
    event_type: str
    scope_type: str
    scope_id: str
    actor_user_id: str | None
    payload: dict[str, Any]
    attempt_count: int
    lease_until: datetime
    created_at: datetime


def retry_delay_seconds(
    attempt_count: int,
    *,
    base_delay_seconds: float = 5.0,
    max_delay_seconds: float = 300.0,
) -> float:
    exponent = max(attempt_count - 1, 0)
    return min(max_delay_seconds, base_delay_seconds * (2**exponent))


class EventRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def rollback(self) -> None:
        """Reset an aborted transaction before durable retry bookkeeping."""
        self.conn.rollback()

    def claim_next(self, *, processing_timeout_seconds: float = 120.0) -> ClaimedEvent | None:
        row = self.conn.execute(
            """
            WITH candidate AS (
                SELECT id
                FROM events
                WHERE status IN ('pending', 'retry')
                  AND available_at <= CURRENT_TIMESTAMP
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE events AS e
            SET status = 'processing',
                attempt_count = e.attempt_count + 1,
                available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                last_error = NULL,
                processed_at = NULL
            FROM candidate
            WHERE e.id = candidate.id
            RETURNING
                e.id, e.event_id, e.event_type, e.scope_type, e.scope_id,
                e.actor_user_id, e.payload, e.attempt_count, e.available_at,
                e.created_at
            """,
            (processing_timeout_seconds,),
        ).fetchone()
        self.conn.commit()
        if row is None:
            return None
        return ClaimedEvent(
            id=int(row[0]),
            event_id=str(row[1]),
            event_type=str(row[2]),
            scope_type=str(row[3]),
            scope_id=str(row[4]),
            actor_user_id=str(row[5]) if row[5] is not None else None,
            payload=dict(row[6]),
            attempt_count=int(row[7]),
            lease_until=row[8],
            created_at=row[9],
        )

    def complete(self, event_id: str) -> bool:
        row = self.conn.execute(
            """
            UPDATE events
            SET status = 'completed',
                processed_at = CURRENT_TIMESTAMP,
                last_error = NULL
            WHERE event_id = %s
              AND status = 'processing'
            RETURNING id
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            existing = self.conn.execute(
                "SELECT status FROM events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
            self.conn.commit()
            return bool(existing and existing[0] == "completed")
        self.conn.commit()
        return True

    def retry(
        self,
        event_id: str,
        error: str,
        *,
        max_attempts: int = 5,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
    ) -> str | None:
        row = self.conn.execute(
            "SELECT attempt_count FROM events WHERE event_id = %s AND status = 'processing'",
            (event_id,),
        ).fetchone()
        if row is None:
            self.conn.commit()
            return None

        attempt_count = int(row[0])
        terminal = attempt_count >= max_attempts
        delay = retry_delay_seconds(
            attempt_count,
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
        )
        status = "failed" if terminal else "retry"
        self.conn.execute(
            """
            UPDATE events
            SET status = %s,
                available_at = CASE
                    WHEN %s THEN CURRENT_TIMESTAMP
                    ELSE CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                END,
                last_error = %s,
                processed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE event_id = %s AND status = 'processing'
            """,
            (status, terminal, delay, error[:4000], terminal, event_id),
        )
        self.conn.commit()
        return status

    def recover_stale(
        self,
        *,
        max_attempts: int = 5,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
    ) -> int:
        rows = self.conn.execute(
            """
            SELECT event_id, attempt_count
            FROM events
            WHERE status = 'processing'
              AND available_at <= CURRENT_TIMESTAMP
            FOR UPDATE SKIP LOCKED
            """
        ).fetchall()
        recovered = 0
        for event_id, attempt_count_raw in rows:
            attempt_count = int(attempt_count_raw)
            terminal = attempt_count >= max_attempts
            delay = retry_delay_seconds(
                attempt_count,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
            status = "failed" if terminal else "retry"
            self.conn.execute(
                """
                UPDATE events
                SET status = %s,
                    available_at = CASE
                        WHEN %s THEN CURRENT_TIMESTAMP
                        ELSE CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    END,
                    last_error = 'processing lease expired',
                    processed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE event_id = %s
                  AND status = 'processing'
                  AND available_at <= CURRENT_TIMESTAMP
                """,
                (status, terminal, delay, terminal, event_id),
            )
            recovered += 1
        self.conn.commit()
        return recovered
