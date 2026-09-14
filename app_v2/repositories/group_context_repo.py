from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ParticipantContext:
    telegram_user_id: str | None
    display_name: str | None = None
    role: str = "member"
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupContext:
    internal_chat_id: int
    telegram_chat_id: str
    title: str | None
    is_whitelisted: bool
    is_active: bool
    silent_until: datetime | None
    profile: dict[str, Any]
    participant: ParticipantContext


class GroupContextRepository:
    """Read/write boundary for Group activation and membership context.

    Telegram ingest owns normal user/chat/member upserts. This repository only
    reads the normalized state and exposes an explicit whitelist mutation for
    administrative/test setup.
    """

    def __init__(self, conn) -> None:
        self.conn = conn

    def load(self, telegram_chat_id: str, actor_telegram_user_id: str | None) -> GroupContext | None:
        row = self.conn.execute(
            """
            SELECT id, telegram_chat_id, title, is_whitelisted, is_active,
                   silent_until, group_profile, chat_type
            FROM chats
            WHERE telegram_chat_id = %s
            """,
            (int(telegram_chat_id),),
        ).fetchone()
        if row is None:
            return None
        if str(row[7]) != "group":
            return GroupContext(
                internal_chat_id=int(row[0]),
                telegram_chat_id=str(row[1]),
                title=row[2],
                is_whitelisted=False,
                is_active=False,
                silent_until=row[5],
                profile=dict(row[6] or {}),
                participant=ParticipantContext(telegram_user_id=actor_telegram_user_id),
            )

        participant = ParticipantContext(telegram_user_id=actor_telegram_user_id)
        if actor_telegram_user_id is not None:
            member = self.conn.execute(
                """
                SELECT u.telegram_user_id, u.display_name, cm.role, cm.participant_profile
                FROM users u
                LEFT JOIN chat_members cm
                  ON cm.user_id = u.id AND cm.chat_id = %s
                WHERE u.telegram_user_id = %s
                """,
                (int(row[0]), int(actor_telegram_user_id)),
            ).fetchone()
            if member is not None:
                participant = ParticipantContext(
                    telegram_user_id=str(member[0]),
                    display_name=member[1],
                    role=str(member[2] or "member"),
                    profile=dict(member[3] or {}),
                )

        return GroupContext(
            internal_chat_id=int(row[0]),
            telegram_chat_id=str(row[1]),
            title=row[2],
            is_whitelisted=bool(row[3]),
            is_active=bool(row[4]),
            silent_until=row[5],
            profile=dict(row[6] or {}),
            participant=participant,
        )

    def set_whitelisted(self, telegram_chat_id: str, enabled: bool) -> bool:
        row = self.conn.execute(
            """
            UPDATE chats
            SET is_whitelisted = %s, updated_at = CURRENT_TIMESTAMP
            WHERE telegram_chat_id = %s AND chat_type = 'group'
            RETURNING id
            """,
            (enabled, int(telegram_chat_id)),
        ).fetchone()
        self.conn.commit()
        return row is not None
