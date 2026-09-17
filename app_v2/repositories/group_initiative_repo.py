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
            WITH ranked_reactions AS (
                SELECT
                    f.id,
                    f.scope_id,
                    f.intervention_id,
                    f.user_id,
                    f.feedback_type,
                    f.created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY f.scope_id, f.intervention_id, f.user_id
                        ORDER BY
                            CASE
                                WHEN (f.payload ->> 'source_event_id') ~ '^tg:[0-9]+$'
                                THEN split_part(f.payload ->> 'source_event_id', ':', 2)::bigint
                                ELSE NULL
                            END DESC NULLS LAST,
                            f.created_at DESC,
                            f.id DESC
                    ) AS rn
                FROM feedback_events f
                WHERE f.scope_id=%s
                  AND f.intervention_id IS NOT NULL
                  AND f.user_id IS NOT NULL
                  AND f.feedback_type LIKE 'reaction_%%'
            )
            SELECT
                (
                    SELECT COUNT(*)
                    FROM feedback_events f
                    WHERE f.scope_id=%s
                      AND f.created_at >= %s
                      AND f.feedback_type = ANY(%s::text[])
                      AND f.feedback_type NOT LIKE 'reaction_%%'
                )
                +
                (
                    SELECT COUNT(*)
                    FROM ranked_reactions r
                    WHERE r.rn=1
                      AND r.created_at >= %s
                      AND r.feedback_type = ANY(%s::text[])
                )
            """,
            (scope_id, scope_id, since, types, since, types),
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
