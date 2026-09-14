from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.outbound import OutboundMessage


@dataclass(frozen=True)
class ClaimedOutbox:
    id: int
    dedupe_key: str
    channel: str
    destination_id: str
    payload: dict[str, Any]
    attempt_count: int
    lease_until: datetime


def retry_delay_seconds(
    attempt_count: int,
    *,
    base_delay_seconds: float = 5.0,
    max_delay_seconds: float = 300.0,
) -> float:
    return min(max_delay_seconds, base_delay_seconds * (2 ** max(attempt_count - 1, 0)))


class OutboxRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def enqueue(self, message: OutboundMessage) -> tuple[int, bool]:
        payload = {
            "message_id": message.message_id,
            "scope_type": message.scope_type.value,
            "scope_id": message.scope_id,
            "text": message.text,
            "reply_to_message_id": message.reply_to_message_id,
            "metadata": message.metadata,
        }
        row = self.conn.execute(
            """
            INSERT INTO outbox(dedupe_key, channel, destination_id, payload, status, available_at)
            VALUES (%s, 'telegram', %s, %s::jsonb, 'pending', CURRENT_TIMESTAMP)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING id
            """,
            (message.dedupe_key, message.scope_id, json.dumps(payload, ensure_ascii=False)),
        ).fetchone()
        if row is not None:
            self.conn.commit()
            return int(row[0]), True

        existing = self.conn.execute(
            "SELECT id FROM outbox WHERE dedupe_key = %s",
            (message.dedupe_key,),
        ).fetchone()
        self.conn.commit()
        if existing is None:
            raise RuntimeError("Outbox dedupe conflict occurred but existing row was not found")
        return int(existing[0]), False

    def claim_next(self, *, processing_timeout_seconds: float = 120.0) -> ClaimedOutbox | None:
        row = self.conn.execute(
            """
            WITH candidate AS (
                SELECT id
                FROM outbox
                WHERE status IN ('pending', 'retry')
                  AND available_at <= CURRENT_TIMESTAMP
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE outbox AS o
            SET status = 'processing',
                attempt_count = o.attempt_count + 1,
                available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                last_error = NULL
            FROM candidate
            WHERE o.id = candidate.id
            RETURNING o.id, o.dedupe_key, o.channel, o.destination_id,
                      o.payload, o.attempt_count, o.available_at
            """,
            (processing_timeout_seconds,),
        ).fetchone()
        self.conn.commit()
        if row is None:
            return None
        return ClaimedOutbox(
            id=int(row[0]),
            dedupe_key=str(row[1]),
            channel=str(row[2]),
            destination_id=str(row[3]),
            payload=dict(row[4]),
            attempt_count=int(row[5]),
            lease_until=row[6],
        )

    def mark_sent(self, outbox_id: int, *, telegram_message_id: int | None = None) -> bool:
        row = self.conn.execute(
            """
            UPDATE outbox
            SET status = 'sent',
                sent_at = CURRENT_TIMESTAMP,
                last_error = NULL,
                telegram_message_id = COALESCE(%s, telegram_message_id)
            WHERE id = %s AND status = 'processing'
            RETURNING id
            """,
            (telegram_message_id, outbox_id),
        ).fetchone()
        if row is None:
            existing = self.conn.execute(
                "SELECT status FROM outbox WHERE id = %s",
                (outbox_id,),
            ).fetchone()
            self.conn.commit()
            return bool(existing and existing[0] == "sent")
        self.conn.commit()
        return True

    def retry(
        self,
        outbox_id: int,
        error: str,
        *,
        max_attempts: int = 5,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
    ) -> str | None:
        row = self.conn.execute(
            "SELECT attempt_count FROM outbox WHERE id = %s AND status = 'processing'",
            (outbox_id,),
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
            UPDATE outbox
            SET status = %s,
                available_at = CASE
                    WHEN %s THEN CURRENT_TIMESTAMP
                    ELSE CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                END,
                last_error = %s
            WHERE id = %s AND status = 'processing'
            """,
            (status, terminal, delay, error[:4000], outbox_id),
        )
        self.conn.commit()
        return status

    def recover_stale(self) -> int:
        row = self.conn.execute(
            """
            WITH stale AS (
                SELECT id
                FROM outbox
                WHERE status = 'processing' AND available_at <= CURRENT_TIMESTAMP
                FOR UPDATE SKIP LOCKED
            )
            UPDATE outbox AS o
            SET status = 'retry',
                available_at = CURRENT_TIMESTAMP,
                last_error = 'outbox processing lease expired'
            FROM stale
            WHERE o.id = stale.id
            RETURNING o.id
            """
        ).fetchall()
        self.conn.commit()
        return len(row)
