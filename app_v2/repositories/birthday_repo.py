from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class BirthdayCandidate:
    scope_id: str
    telegram_user_id: str
    display_name: str | None
    day: int
    month: int
    source: str
    participant_profile: dict[str, Any]


class BirthdayRepository:
    """Group-scoped structured birthday state and durable birthday events."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def rollback(self) -> None:
        self.conn.rollback()

    def _load_member(
        self,
        scope_id: str,
        telegram_user_id: str,
        *,
        for_update: bool = False,
    ) -> tuple[int, int, dict[str, Any]] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.conn.execute(
            f"""
            SELECT c.id, u.id, cm.participant_profile
            FROM chats c
            JOIN chat_members cm ON cm.chat_id=c.id
            JOIN users u ON u.id=cm.user_id
            WHERE c.chat_type='group'
              AND c.telegram_chat_id=%s
              AND u.telegram_user_id=%s
            {suffix}
            """,
            (int(scope_id), int(telegram_user_id)),
        ).fetchone()
        if row is None:
            return None
        return int(row[0]), int(row[1]), dict(row[2] or {})

    def _save_profile(self, chat_id: int, user_id: int, profile: dict[str, Any]) -> None:
        self.conn.execute(
            """
            UPDATE chat_members
            SET participant_profile=%s::jsonb,
                last_seen_at=COALESCE(last_seen_at, CURRENT_TIMESTAMP)
            WHERE chat_id=%s AND user_id=%s
            """,
            (
                json.dumps(profile, ensure_ascii=False, default=str),
                chat_id,
                user_id,
            ),
        )
        self.conn.commit()

    def get_participant_profile(
        self,
        scope_id: str,
        telegram_user_id: str,
    ) -> dict[str, Any] | None:
        loaded = self._load_member(scope_id, telegram_user_id)
        return None if loaded is None else dict(loaded[2])

    def save_explicit_birthday(
        self,
        *,
        scope_id: str,
        telegram_user_id: str,
        day: int,
        month: int,
        year: int | None,
        now: datetime,
    ) -> bool:
        loaded = self._load_member(scope_id, telegram_user_id, for_update=True)
        if loaded is None:
            self.conn.rollback()
            return False
        chat_id, user_id, profile = loaded
        profile["birthday"] = {
            "day": int(day),
            "month": int(month),
            "year": int(year) if year is not None else None,
            "source": "explicit",
            "confirmed": True,
            "updated_at": now.isoformat(),
        }
        profile.setdefault("birthday_congratulations", {"enabled": True})
        self._save_profile(chat_id, user_id, profile)
        return True

    def clear_birthday(
        self,
        *,
        scope_id: str,
        telegram_user_id: str,
        now: datetime,
    ) -> bool:
        loaded = self._load_member(scope_id, telegram_user_id, for_update=True)
        if loaded is None:
            self.conn.rollback()
            return False
        chat_id, user_id, profile = loaded
        existed = "birthday" in profile
        profile.pop("birthday", None)
        profile["birthday_lookup"] = {
            "checked_at": now.isoformat(),
            "status": "cleared_by_user",
        }
        self._save_profile(chat_id, user_id, profile)
        return existed

    def set_congratulations_enabled(
        self,
        *,
        scope_id: str,
        telegram_user_id: str,
        enabled: bool,
        now: datetime,
    ) -> bool:
        loaded = self._load_member(scope_id, telegram_user_id, for_update=True)
        if loaded is None:
            self.conn.rollback()
            return False
        chat_id, user_id, profile = loaded
        profile["birthday_congratulations"] = {
            "enabled": bool(enabled),
            "updated_at": now.isoformat(),
        }
        self._save_profile(chat_id, user_id, profile)
        return True

    @staticmethod
    def _parse_checked_at(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            return None
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result

    def telegram_refresh_due(
        self,
        *,
        scope_id: str,
        telegram_user_id: str,
        now: datetime,
        normal_days: int = 30,
        error_days: int = 1,
    ) -> bool:
        loaded = self._load_member(scope_id, telegram_user_id)
        if loaded is None:
            return False
        profile = loaded[2]
        birthday = profile.get("birthday")
        if isinstance(birthday, dict) and birthday.get("source") == "explicit":
            return False

        lookup = profile.get("birthday_lookup")
        if not isinstance(lookup, dict):
            return True
        checked_at = self._parse_checked_at(lookup.get("checked_at"))
        if checked_at is None:
            return True

        status = str(lookup.get("status") or "")
        age = now - checked_at
        interval = timedelta(days=error_days if status == "error" else normal_days)
        return age >= interval

    def record_telegram_lookup(
        self,
        *,
        scope_id: str,
        telegram_user_id: str,
        status: str,
        now: datetime,
        birthdate: Any | None = None,
    ) -> bool:
        loaded = self._load_member(scope_id, telegram_user_id, for_update=True)
        if loaded is None:
            self.conn.rollback()
            return False
        chat_id, user_id, profile = loaded
        profile["birthday_lookup"] = {
            "checked_at": now.isoformat(),
            "status": str(status),
        }

        existing = profile.get("birthday")
        explicit = isinstance(existing, dict) and existing.get("source") == "explicit"
        if birthdate is not None and not explicit:
            profile["birthday"] = {
                "day": int(birthdate.day),
                "month": int(birthdate.month),
                "year": (
                    int(birthdate.year)
                    if getattr(birthdate, "year", None) is not None
                    else None
                ),
                "source": "telegram_profile",
                "confirmed": True,
                "updated_at": now.isoformat(),
            }
            profile.setdefault("birthday_congratulations", {"enabled": True})
        elif status in {"not_shared", "unavailable"} and not explicit:
            # Do not keep using a Telegram-sourced birthday once the bot can no
            # longer see it. This makes Telegram privacy changes effective.
            if isinstance(existing, dict) and existing.get("source") == "telegram_profile":
                profile.pop("birthday", None)

        self._save_profile(chat_id, user_id, profile)
        return True

    def list_candidates(self, *, limit: int = 500) -> list[BirthdayCandidate]:
        rows = self.conn.execute(
            """
            SELECT
                c.telegram_chat_id::text,
                u.telegram_user_id::text,
                u.display_name,
                cm.participant_profile
            FROM chats c
            JOIN chat_members cm ON cm.chat_id=c.id
            JOIN users u ON u.id=cm.user_id
            WHERE c.chat_type='group'
              AND c.is_whitelisted=TRUE
              AND c.is_active=TRUE
              AND cm.participant_profile ? 'birthday'
            ORDER BY c.id, cm.user_id
            LIMIT %s
            """,
            (max(1, min(int(limit), 5000)),),
        ).fetchall()

        result: list[BirthdayCandidate] = []
        for row in rows:
            profile = dict(row[3] or {})
            birthday = profile.get("birthday")
            if not isinstance(birthday, dict):
                continue
            controls = profile.get("birthday_congratulations")
            if isinstance(controls, dict) and controls.get("enabled") is False:
                continue
            try:
                day = int(birthday.get("day"))
                month = int(birthday.get("month"))
            except (TypeError, ValueError):
                continue
            source = str(birthday.get("source") or "unknown")
            result.append(
                BirthdayCandidate(
                    scope_id=str(row[0]),
                    telegram_user_id=str(row[1]),
                    display_name=(str(row[2]).strip() if row[2] else None),
                    day=day,
                    month=month,
                    source=source,
                    participant_profile=profile,
                )
            )
        return result

    def enqueue_due(
        self,
        candidate: BirthdayCandidate,
        *,
        local_year: int,
        now: datetime,
    ) -> bool:
        event_id = (
            f"birthday:{candidate.scope_id}:"
            f"{candidate.telegram_user_id}:{int(local_year)}"
        )
        payload = {
            "event_id": event_id,
            "event_type": "birthday_due",
            "occurred_at": now.isoformat(),
            "scope_type": "group",
            "scope_id": candidate.scope_id,
            "actor_user_id": candidate.telegram_user_id,
            "message_id": None,
            "reply_to_message_id": None,
            "text": None,
            "metadata": {
                "synthetic": True,
                "birthday_due": True,
                "birthday_user_id": candidate.telegram_user_id,
                "birthday_name": candidate.display_name,
                "birthday_day": candidate.day,
                "birthday_month": candidate.month,
                "birthday_source": candidate.source,
                "age_allowed": False,
            },
        }
        row = self.conn.execute(
            """
            INSERT INTO events(
                event_id, telegram_update_id, event_type, scope_type, scope_id,
                actor_user_id, payload, status, available_at
            )
            VALUES (%s, NULL, 'birthday_due', 'group', %s, %s,
                    %s::jsonb, 'pending', CURRENT_TIMESTAMP)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
            """,
            (
                event_id,
                candidate.scope_id,
                candidate.telegram_user_id,
                json.dumps(payload, ensure_ascii=False),
            ),
        ).fetchone()
        self.conn.commit()
        return row is not None
