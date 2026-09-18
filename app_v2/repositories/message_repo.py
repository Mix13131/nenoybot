from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app_v2.domain.enums import ScopeType


@dataclass(frozen=True)
class HotMessage:
    message_id: str
    author_user_id: str | None
    text: str
    created_at: datetime
    reply_to_message_id: str | None = None


class MessageRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    @staticmethod
    def _rows_to_messages(rows) -> list[HotMessage]:
        result = [
            HotMessage(
                message_id=str(row[0]),
                author_user_id=str(row[1]) if row[1] is not None else None,
                text=str(row[2]),
                created_at=row[3],
                reply_to_message_id=str(row[4]) if row[4] is not None else None,
            )
            for row in rows
        ]
        result.reverse()
        return result

    def recent_for_scope(
        self,
        scope_type: ScopeType,
        scope_id: str,
        *,
        limit: int = 100,
    ) -> list[HotMessage]:
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        chat_type = "private" if scope_type is ScopeType.PERSONAL else "group"
        rows = self.conn.execute(
            """
            SELECT m.telegram_message_id,
                   u.telegram_user_id,
                   COALESCE(m.text, ''),
                   m.created_at,
                   m.reply_to_message_id
            FROM messages m
            JOIN chats c ON c.id = m.chat_id
            LEFT JOIN users u ON u.id = m.user_id
            WHERE c.chat_type=%s
              AND c.telegram_chat_id::text=%s
              AND m.text IS NOT NULL
            ORDER BY m.created_at DESC, m.telegram_message_id DESC
            LIMIT %s
            """,
            (chat_type, scope_id, max(1, min(limit, 200))),
        ).fetchall()
        return self._rows_to_messages(rows)

    def recent_before_event(
        self,
        scope_type: ScopeType,
        scope_id: str,
        *,
        before: datetime,
        before_message_id: str | None = None,
        boundary_event_id: str | None = None,
        limit: int = 12,
    ) -> list[HotMessage]:
        """Return immutable message versions from before an event boundary.

        Inbound ``events.payload`` is append-only and therefore preserves the
        text of every Telegram message/edit version. ``messages.text`` is a
        mutable projection and must never be used for replayable mapper input.
        A persisted boundary event is required so Telegram update order can
        exclude later edits deterministically, including same-second updates.
        """
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware")

        bounded_limit = max(1, min(limit, 50))
        if not boundary_event_id:
            return []

        rows = self.conn.execute(
            """
            WITH boundary AS (
                SELECT telegram_update_id
                FROM events
                WHERE event_id=%s AND scope_type=%s AND scope_id=%s
                  AND telegram_update_id IS NOT NULL
            ), versions AS (
                SELECT
                    e.payload ->> 'message_id' AS message_id,
                    e.payload ->> 'actor_user_id' AS author_user_id,
                    e.payload ->> 'text' AS text,
                    (e.payload ->> 'occurred_at')::timestamptz AS occurred_at,
                    e.payload ->> 'reply_to_message_id' AS reply_to_message_id,
                    e.telegram_update_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.payload ->> 'message_id'
                        ORDER BY e.telegram_update_id DESC, e.id DESC
                    ) AS version_rank
                FROM events e
                CROSS JOIN boundary b
                WHERE e.scope_type=%s AND e.scope_id=%s
                  AND e.telegram_update_id IS NOT NULL
                  AND e.telegram_update_id < b.telegram_update_id
                  AND e.payload ->> 'message_id' IS NOT NULL
                  AND e.payload ->> 'text' IS NOT NULL
            )
            SELECT message_id, author_user_id, text, occurred_at, reply_to_message_id
            FROM versions
            WHERE version_rank=1
            ORDER BY occurred_at DESC,
                     CASE WHEN message_id ~ '^[0-9]+$' THEN message_id::numeric END DESC NULLS LAST,
                     message_id DESC,
                     telegram_update_id DESC
            LIMIT %s
            """,
            (
                boundary_event_id,
                scope_type.value,
                scope_id,
                scope_type.value,
                scope_id,
                bounded_limit,
            ),
        ).fetchall()
        return self._rows_to_messages(rows)
