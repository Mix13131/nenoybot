from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.adapters.telegram_profile import TelegramProfileLookupError


_MONTHS = {
    "января": 1, "январь": 1, "янв": 1,
    "февраля": 2, "февраль": 2, "фев": 2,
    "марта": 3, "март": 3, "мар": 3,
    "апреля": 4, "апрель": 4, "апр": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6, "июн": 6,
    "июля": 7, "июль": 7, "июл": 7,
    "августа": 8, "август": 8, "авг": 8,
    "сентября": 9, "сентябрь": 9, "сен": 9, "сент": 9,
    "октября": 10, "октябрь": 10, "окт": 10,
    "ноября": 11, "ноябрь": 11, "ноя": 11,
    "декабря": 12, "декабрь": 12, "дек": 12,
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted((re.escape(item) for item in _MONTHS), key=len, reverse=True))
_SELF_PREFIX = (
    r"(?:у\s+меня(?:\s+(?:день\s+рождения|др))?|"
    r"мой\s+(?:день\s+рождения|др)|"
    r"моя\s+дата\s+рождения|"
    r"я\s+родил(?:ся|ась))"
)
_TEXT_DATE_RE = re.compile(
    rf"(?i)\b{_SELF_PREFIX}\b\s*(?:[-—:=]\s*)?"
    rf"(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_PATTERN})"
    rf"(?:\s+(?P<year>\d{{4}}))?\b"
)
_NUMERIC_DATE_RE = re.compile(
    rf"(?i)\b{_SELF_PREFIX}\b\s*(?:[-—:=]\s*)?"
    r"(?P<day>\d{1,2})[./-](?P<month>\d{1,2})"
    r"(?:[./-](?P<year>\d{4}))?\b"
)
_FORGET_RE = re.compile(
    r"(?i)\b(?:забудь|удали|убери)\b.{0,50}\b"
    r"(?:мой\s+)?(?:день\s+рождения|др|дату\s+рождения)\b"
)
_DISABLE_RE = re.compile(
    r"(?i)\b(?:не\s+поздравляй\s+меня|не\s+надо\s+меня\s+поздравлять|"
    r"не\s+отмечай\s+мой\s+(?:день\s+рождения|др))\b"
)
_ENABLE_RE = re.compile(
    r"(?i)\b(?:поздравляй\s+меня|можешь\s+поздравлять\s+меня|"
    r"отмечай\s+мой\s+(?:день\s+рождения|др))\b"
)
_HUMAN_GROUP_EVENTS = {
    EventType.GROUP_MESSAGE,
    EventType.DIRECT_MENTION,
    EventType.REPLY_TO_BOT,
    EventType.EDITED_MESSAGE,
}


@dataclass(frozen=True)
class ParsedBirthday:
    day: int
    month: int
    year: int | None


class BirthdayService:
    """Learn privacy-visible birthdays and emit one group event per birthday/year."""

    def __init__(
        self,
        *,
        repo: Any,
        telegram_profile_client: Any,
        group_context_repo: Any,
        connector_resolver: Any | None = None,
    ) -> None:
        self.repo = repo
        self.telegram_profile_client = telegram_profile_client
        self.group_context_repo = group_context_repo
        self.connector_resolver = connector_resolver

    @staticmethod
    def _enabled(connector_config: Any | None, group_profile: dict[str, Any]) -> bool:
        if connector_config is not None:
            return bool(getattr(connector_config.capabilities, "birthdays", False))
        raw = group_profile.get("birthday_enabled", True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "no", "off"}
        return True

    @staticmethod
    def _birthday_hour(connector_config: Any | None, group_profile: dict[str, Any]) -> int:
        if connector_config is not None:
            return max(0, min(23, int(getattr(connector_config.behavior, "birthday_hour", 9))))
        try:
            value = int(group_profile.get("birthday_hour", 9))
        except (TypeError, ValueError):
            value = 9
        return max(0, min(23, value))

    @staticmethod
    def _timezone(connector_config: Any | None, group_profile: dict[str, Any]) -> str:
        if connector_config is not None:
            return str(getattr(connector_config.behavior, "timezone", "") or "").strip()
        return str(group_profile.get("timezone") or "").strip()

    @staticmethod
    def parse_self_birthday(text: str, *, now: datetime) -> ParsedBirthday | None:
        match = _TEXT_DATE_RE.search(text) or _NUMERIC_DATE_RE.search(text)
        if match is None:
            return None
        try:
            day = int(match.group("day"))
            month_raw = match.group("month")
            if month_raw.isdigit():
                month = int(month_raw)
            else:
                month = _MONTHS[month_raw.casefold()]
            year_raw = match.group("year")
            year = int(year_raw) if year_raw else None
            date(year or 2000, month, day)
        except (KeyError, TypeError, ValueError):
            return None

        if year is not None:
            current_year = now.year
            if year > current_year or year < current_year - 150:
                return None
        return ParsedBirthday(day=day, month=month, year=year)

    def observe_group_event(
        self,
        event: EventEnvelope,
        *,
        group_context: Any,
        connector_config: Any | None,
        now: datetime,
    ) -> dict[str, Any] | None:
        if (
            event.scope_type is not ScopeType.GROUP
            or event.event_type not in _HUMAN_GROUP_EVENTS
            or not event.actor_user_id
            or not self._enabled(connector_config, dict(group_context.profile or {}))
        ):
            return None

        text = (event.text or "").strip()
        explicit_address = bool(
            event.event_type in {EventType.DIRECT_MENTION, EventType.REPLY_TO_BOT}
            or event.metadata.get("direct_mention")
            or event.metadata.get("reply_to_bot")
        )

        if explicit_address and text:
            if _FORGET_RE.search(text):
                changed = self.repo.clear_birthday(
                    scope_id=event.scope_id,
                    telegram_user_id=event.actor_user_id,
                    now=now,
                )
                return {
                    "status": "cleared" if changed else "already_empty",
                    "operation": "clear",
                }

            if _DISABLE_RE.search(text):
                changed = self.repo.set_congratulations_enabled(
                    scope_id=event.scope_id,
                    telegram_user_id=event.actor_user_id,
                    enabled=False,
                    now=now,
                )
                return {
                    "status": "succeeded" if changed else "failed",
                    "operation": "disable_congratulations",
                }

            if _ENABLE_RE.search(text):
                changed = self.repo.set_congratulations_enabled(
                    scope_id=event.scope_id,
                    telegram_user_id=event.actor_user_id,
                    enabled=True,
                    now=now,
                )
                return {
                    "status": "succeeded" if changed else "failed",
                    "operation": "enable_congratulations",
                }

            parsed = self.parse_self_birthday(text, now=now)
            if parsed is not None:
                changed = self.repo.save_explicit_birthday(
                    scope_id=event.scope_id,
                    telegram_user_id=event.actor_user_id,
                    day=parsed.day,
                    month=parsed.month,
                    year=parsed.year,
                    now=now,
                )
                return {
                    "status": "succeeded" if changed else "failed",
                    "operation": "save",
                    "day": parsed.day,
                    "month": parsed.month,
                    "year": parsed.year,
                    "source": "explicit",
                }

        if not self.repo.telegram_refresh_due(
            scope_id=event.scope_id,
            telegram_user_id=event.actor_user_id,
            now=now,
        ):
            return None

        try:
            lookup = self.telegram_profile_client.get_birthdate(event.actor_user_id)
        except TelegramProfileLookupError:
            self.repo.record_telegram_lookup(
                scope_id=event.scope_id,
                telegram_user_id=event.actor_user_id,
                status="error",
                now=now,
            )
            return None

        self.repo.record_telegram_lookup(
            scope_id=event.scope_id,
            telegram_user_id=event.actor_user_id,
            status=lookup.status,
            now=now,
            birthdate=lookup.birthdate,
        )
        return None

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        for candidate in self.repo.list_candidates(limit=1000):
            group_context = self.group_context_repo.load(
                candidate.scope_id,
                candidate.telegram_user_id,
            )
            if (
                group_context is None
                or not group_context.is_whitelisted
                or not group_context.is_active
            ):
                continue
            if group_context.silent_until and group_context.silent_until > current:
                continue

            connector_config = None
            if self.connector_resolver is not None:
                try:
                    connector_config = self.connector_resolver.resolve(group_context)
                except Exception:
                    continue

            group_profile = dict(group_context.profile or {})
            if not self._enabled(connector_config, group_profile):
                continue

            timezone_name = self._timezone(connector_config, group_profile)
            if not timezone_name:
                continue
            try:
                local_tz = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                continue

            local_now = current.astimezone(local_tz)
            if local_now.month != candidate.month or local_now.day != candidate.day:
                continue

            birthday_hour = self._birthday_hour(connector_config, group_profile)
            # Never wake a group late at night just because the worker was down.
            if local_now.hour < birthday_hour or local_now.hour >= 21:
                continue

            if self.repo.enqueue_due(
                candidate,
                local_year=local_now.year,
                now=current,
            ):
                return True
        return False
