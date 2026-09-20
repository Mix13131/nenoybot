from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SilenceWakeupCandidate:
    scope_id: str
    profile: dict[str, Any]
    silent_until: datetime | None
    last_human_message_id: int
    last_human_message_at: datetime
    last_human_excerpt: str
    last_successful_wakeup_at: datetime | None
    last_attempt_at: datetime | None


class GroupSilenceWakeupRepository:
    """Durable PostgreSQL boundary for proactive wakeup opportunities."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def list_candidates(self, *, limit: int = 50) -> list[SilenceWakeupCandidate]:
        rows = self.conn.execute(
            """
            SELECT
                c.telegram_chat_id::text,
                c.group_profile,
                c.silent_until,
                last_human.id,
                last_human.created_at,
                LEFT(COALESCE(last_human.text, ''), 700),
                (
                    SELECT MAX(i.created_at)
                    FROM interventions i
                    JOIN events e ON e.event_id=i.event_id
                    WHERE i.scope_type='group'
                      AND i.scope_id=c.telegram_chat_id::text
                      AND i.primary_action='reply'
                      AND e.event_type='group_silence_wakeup'
                ) AS last_successful_wakeup_at,
                (
                    SELECT MAX(e.created_at)
                    FROM events e
                    WHERE e.scope_type='group'
                      AND e.scope_id=c.telegram_chat_id::text
                      AND e.event_type='group_silence_wakeup'
                ) AS last_attempt_at
            FROM chats c
            JOIN LATERAL (
                SELECT m.id, m.created_at, m.text
                FROM messages m
                WHERE m.chat_id=c.id
                  AND m.user_id IS NOT NULL
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 1
            ) AS last_human ON TRUE
            WHERE c.chat_type='group'
              AND c.is_whitelisted=TRUE
              AND c.is_active=TRUE
            ORDER BY last_human.created_at ASC
            LIMIT %s
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [
            SilenceWakeupCandidate(
                scope_id=str(row[0]),
                profile=dict(row[1] or {}),
                silent_until=row[2],
                last_human_message_id=int(row[3]),
                last_human_message_at=row[4],
                last_human_excerpt=str(row[5] or ""),
                last_successful_wakeup_at=row[6],
                last_attempt_at=row[7],
            )
            for row in rows
        ]

    def count_successful_since(self, scope_id: str, since: datetime) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM interventions i
            JOIN events e ON e.event_id=i.event_id
            WHERE i.scope_type='group'
              AND i.scope_id=%s
              AND i.primary_action='reply'
              AND e.event_type='group_silence_wakeup'
              AND i.created_at >= %s
            """,
            (scope_id, since),
        ).fetchone()
        return int(row[0] if row else 0)

    def enqueue(
        self,
        candidate: SilenceWakeupCandidate,
        *,
        now: datetime,
        attempt_gap_minutes: int,
        silence_minutes: int,
    ) -> bool:
        slot_seconds = max(60, int(attempt_gap_minutes) * 60)
        slot = int(now.timestamp()) // slot_seconds
        event_id = (
            f"silence:{candidate.scope_id}:"
            f"{candidate.last_human_message_id}:{slot}"
        )
        payload = {
            "event_id": event_id,
            "event_type": "group_silence_wakeup",
            "occurred_at": now.isoformat(),
            "scope_type": "group",
            "scope_id": candidate.scope_id,
            "actor_user_id": None,
            "message_id": None,
            "reply_to_message_id": None,
            "text": None,
            "metadata": {
                "synthetic": True,
                "silence_wakeup": True,
                "silence_minutes": int(silence_minutes),
                "last_human_message_at": candidate.last_human_message_at.isoformat(),
                "last_human_excerpt": candidate.last_human_excerpt,
            },
        }
        row = self.conn.execute(
            """
            INSERT INTO events(
                event_id, telegram_update_id, event_type, scope_type, scope_id,
                actor_user_id, payload, status, available_at
            )
            VALUES (%s, NULL, 'group_silence_wakeup', 'group', %s, NULL,
                    %s::jsonb, 'pending', CURRENT_TIMESTAMP)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
            """,
            (
                event_id,
                candidate.scope_id,
                json.dumps(payload, ensure_ascii=False),
            ),
        ).fetchone()
        self.conn.commit()
        return row is not None
