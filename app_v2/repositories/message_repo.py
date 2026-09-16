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
        limit: int = 12,
    ) -> list[HotMessage]:
        """Return only messages logically before an event in the same scope.

        Telegram message ids provide a deterministic tie-break when several
        messages share the same second. If an id cannot be parsed, fail closed
        to a strict timestamp cutoff instead of risking future-message leakage.
        """
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware")

        chat_type = "private" if scope_type is ScopeType.PERSONAL else "group"
        bounded_limit = max(1, min(limit, 50))
        try:
            boundary_id = int(before_message_id) if before_message_id is not None else None
        except (TypeError, ValueError):
            boundary_id = None

        if boundary_id is None:
            cutoff_sql = "m.created_at < %s"
            cutoff_params = (before,)
        else:
            cutoff_sql = "(m.created_at < %s OR (m.created_at = %s AND m.telegram_message_id < %s))"
            cutoff_params = (before, before, boundary_id)

        rows = self.conn.execute(
            f"""
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
              AND {cutoff_sql}
            ORDER BY m.created_at DESC, m.telegram_message_id DESC
            LIMIT %s
            """,
            (chat_type, scope_id, *cutoff_params, bounded_limit),
        ).fetchall()
        return self._rows_to_messages(rows)
