from __future__ import annotations

from datetime import datetime
from typing import Iterable


_DIRECT_REASON_CODES = ("direct_mention", "reply_to_bot", "question_to_bot")


class GroupInitiativeRepository:
    """PostgreSQL reads needed by adaptive Group initiative policy."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def count_unsolicited_since(self, scope_id: str, since: datetime) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM interventions
            WHERE scope_type='group'
              AND scope_id=%s
              AND primary_action='reply'
              AND created_at >= %s
              AND NOT (reason_codes ?| %s::text[])
            """,
            (scope_id, since, list(_DIRECT_REASON_CODES)),
        ).fetchone()
        return int(row[0] if row else 0)

    def last_unsolicited_at(self, scope_id: str) -> datetime | None:
        row = self.conn.execute(
            """
            SELECT MAX(created_at)
            FROM interventions
            WHERE scope_type='group'
              AND scope_id=%s
              AND primary_action='reply'
              AND NOT (reason_codes ?| %s::text[])
            """,
            (scope_id, list(_DIRECT_REASON_CODES)),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def count_feedback_since(
        self,
        scope_id: str,
        feedback_types: Iterable[str],
        since: datetime,
    ) -> int:
        types = [item for item in feedback_types if item]
        if not types:
            return 0
        row = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM feedback_events
            WHERE scope_id=%s
              AND created_at >= %s
              AND feedback_type = ANY(%s::text[])
            """,
            (scope_id, since, types),
        ).fetchone()
        return int(row[0] if row else 0)

    def count_messages_since(self, scope_id: str, since: datetime) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN chats c ON c.id=m.chat_id
            WHERE c.telegram_chat_id=%s
              AND c.chat_type='group'
              AND m.created_at >= %s
            """,
            (int(scope_id), since),
        ).fetchone()
        return int(row[0] if row else 0)

    def set_silent_until(self, scope_id: str, silent_until: datetime) -> bool:
        row = self.conn.execute(
            """
            UPDATE chats
            SET silent_until = GREATEST(COALESCE(silent_until, %s), %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_chat_id=%s AND chat_type='group'
            RETURNING id
            """,
            (silent_until, silent_until, int(scope_id)),
        ).fetchone()
        self.conn.commit()
        return row is not None
