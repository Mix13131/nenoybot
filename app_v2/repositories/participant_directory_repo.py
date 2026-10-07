from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


_UNSAFE_FORMATTING_RE = re.compile(r"([\\_*\[\]()~`>#+\-=|{}.!])")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def safe_participant_label(display_name: str | None, username: str | None) -> str:
    """Return bounded, formatting-neutral observed data, never an identifier."""
    raw = (display_name or "").strip() or (f"@{username.strip().lstrip('@')}" if username else "")
    if not raw:
        return "неизвестный участник"
    normalized = " ".join(_CONTROL_RE.sub(" ", raw).split())[:80]
    return _UNSAFE_FORMATTING_RE.sub(r"\\\1", normalized) or "неизвестный участник"


@dataclass(frozen=True)
class DirectoryParticipant:
    label: str
    username: str | None
    role: str
    first_seen_at: datetime
    last_seen_at: datetime | None
    profile: dict[str, Any]

    def as_context(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "username": safe_participant_label(None, self.username) if self.username else None,
            "role": self.role,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_observed_activity_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


@dataclass(frozen=True)
class AliasResolution:
    status: Literal["found", "ambiguous", "not_found"]
    participant: DirectoryParticipant | None = None


class ParticipantDirectoryRepository:
    """Read observed identities only through their current group membership."""

    def __init__(self, conn) -> None:
        self.conn = conn

    @staticmethod
    def _participant(row: tuple[Any, ...]) -> DirectoryParticipant:
        username = str(row[1]) if row[1] else None
        return DirectoryParticipant(
            label=safe_participant_label(row[0], username), username=username,
            role=str(row[2] or "member"), profile=dict(row[3] or {}),
            first_seen_at=row[4], last_seen_at=row[5],
        )

    def list_recent(self, telegram_chat_id: str, *, limit: int = 20) -> list[DirectoryParticipant]:
        rows = self.conn.execute(
            """
            SELECT cm.current_display_name, cm.current_username, cm.role,
                   cm.participant_profile, cm.first_seen_at, cm.last_seen_at
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id
            WHERE c.telegram_chat_id = %s AND c.chat_type = 'group'
            ORDER BY cm.last_seen_at DESC NULLS LAST,
                     lower(COALESCE(cm.current_display_name, cm.current_username, '')) ASC,
                     cm.user_id ASC
            LIMIT %s
            """,
            (int(telegram_chat_id), max(1, min(int(limit), 50))),
        ).fetchall()
        return [self._participant(row) for row in rows]

    def resolve_alias(self, telegram_chat_id: str, alias: str) -> AliasResolution:
        needle = alias.strip().lstrip("@").casefold()
        if not needle:
            return AliasResolution("not_found")
        rows = self.conn.execute(
            """
            SELECT cm.current_display_name, cm.current_username, cm.role,
                   cm.participant_profile, cm.first_seen_at, cm.last_seen_at
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id
            WHERE c.telegram_chat_id = %s AND c.chat_type = 'group'
              AND (lower(COALESCE(cm.current_username, '')) = %s
                OR lower(COALESCE(cm.current_display_name, '')) = %s
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(cm.aliases) item
                    WHERE lower(COALESCE(item ->> 'value', '')) = %s))
            ORDER BY cm.last_seen_at DESC NULLS LAST
            LIMIT 2
            """,
            (int(telegram_chat_id), needle, needle, needle),
        ).fetchall()
        if not rows:
            return AliasResolution("not_found")
        if len(rows) > 1:
            return AliasResolution("ambiguous")
        return AliasResolution("found", self._participant(rows[0]))

