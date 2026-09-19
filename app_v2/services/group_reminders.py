from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.calendar_schedule import (
    CalendarSchedule, next_calendar_occurrence, resolve_local, validate_timezone,
)


_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,32})")
_REMINDER_INTENT_RE = re.compile(r"\b(?:напоминай|напомни|присылай)\b", flags=re.IGNORECASE)
_CANCEL_REMINDER_RE = re.compile(
    r"(?:"
    r"\b(?:отмени|отменяй|останови|остановить|хватит|перестань|прекрати|стоп|достаточно)\b.{0,120}\bнапомин\w*"
    r"|\bне\s+напоминай\b"
    r"|\bгоршочек[\s,]+не\s+вари\b.{0,160}\bнапомин\w*"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)
_STOP_REPLY_RE = re.compile(
    r"^\s*(?:стоп|хватит|достаточно|прекрати|отмени|останови|не\s+надо|не\s+напоминай)\s*[.!?…]*\s*$",
    flags=re.IGNORECASE,
)
_EVERY_HALF_HOUR_RE = re.compile(r"\bкажд\w*\s+пол\s*час", flags=re.IGNORECASE)
_EVERY_INTERVAL_RE = re.compile(
    r"\bкажд\w*\s+(?:(\d{1,3})\s*)?(минут\w*|час\w*)",
    flags=re.IGNORECASE,
)
_AFTER_INTERVAL_RE = re.compile(
    r"\bчерез\s+(\d{1,3})\s*(минут\w*|час\w*)",
    flags=re.IGNORECASE,
)
_CALENDAR_RE = re.compile(
    r"(?P<kind>кажд(?:ый день|ое утро)|по будням|каждую пятницу|сегодня|завтра)"
    r".*?\bв\s+(?P<hour>[01]?\d|2[0-3])"
    r"(?::(?P<minute>[0-5]\d)|(?!:))"
    r"(?:\s*(?P<period>утра|утром|вечера|вечером|дня|днём|днем|ночи|ночью))?"
    r"(?![\d:])",
    flags=re.IGNORECASE,
)
_CALENDAR_KIND_RE = re.compile(
    r"\b(каждый день|каждое утро|по будням|каждую пятницу|сегодня|завтра)\b",
    flags=re.IGNORECASE,
)
_CALENDAR_ALT_BRIDGE_RE = re.compile(
    r"^\s*[,;/]?\s*(?:или|либо)\s*$",
    flags=re.IGNORECASE,
)
_CLOCK_ATTEMPT_RE = re.compile(
    r"(?:(?<!\w)в\s+|(?<!\w)(?:или|либо)\s+)"
    r"(?:[01]?\d|2[0-3])(?::[0-5]\d)?(?![\d:])",
    flags=re.IGNORECASE,
)
_TIMEZONE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_+./-])(UTC|[A-Za-z][A-Za-z0-9_+.-]*/[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*)(?![A-Za-z0-9_+./-])"
)
_TZ_ALIASES = {"по московскому времени": "Europe/Moscow"}
_MSK_RE = re.compile(r"(?<!\w)мск(?!\w)", flags=re.IGNORECASE)

_MIN_RECURRING_INTERVAL_SECONDS = 15 * 60
_MAX_GROUP_REMINDER_OCCURRENCES = 4


@dataclass(frozen=True)
class GroupReminderAction:
    status: str
    reminder_id: int | None = None
    recurring: bool = False
    interval_seconds: int | None = None
    target_username: str | None = None
    due_at: datetime | None = None
    stop_on_reply: bool = False
    cancelled_count: int = 0
    reason: str | None = None
    timezone: str | None = None
    recurrence_rule: str | None = None

    def as_action_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reminder_id": self.reminder_id,
            "recurring": self.recurring,
            "interval_seconds": self.interval_seconds,
            "target_username": self.target_username,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "stop_on_reply": self.stop_on_reply,
            "cancelled_count": self.cancelled_count,
            "reason": self.reason,
            "timezone": self.timezone,
            "recurrence_rule": self.recurrence_rule,
        }


class GroupReminderService:
    """Deterministic action layer for reminders requested inside a group.

    Chat stop controls are deliberately fail-safe: replying `стоп` to a bot
    reminder cancels that exact chain, while an explicit reminder-stop command
    cancels matching active reminders in the current group. Any participant can
    stop a noisy chain; social safety is more important than reminder ownership.
    """

    def __init__(self, reminder_repo: Any) -> None:
        self.reminder_repo = reminder_repo

    def cancel_on_response(self, event: EventEnvelope) -> int:
        if event.scope_type is not ScopeType.GROUP:
            return 0
        if event.event_type in {
            EventType.REMINDER_DUE,
            EventType.REACTION_ADDED,
            EventType.REACTION_REMOVED,
        }:
            return 0
        if not event.message_id:
            return 0
        username = str(event.metadata.get("actor_username") or "").strip()
        return self.reminder_repo.cancel_waiting_for_response(
            scope_id=event.scope_id,
            actor_user_id=event.actor_user_id,
            actor_username=username or None,
        )

    def maybe_schedule(
        self,
        event: EventEnvelope,
        *,
        now: datetime,
    ) -> GroupReminderAction | None:
        if event.scope_type is not ScopeType.GROUP:
            return None
        if event.event_type not in {EventType.DIRECT_MENTION, EventType.REPLY_TO_BOT}:
            return None

        text = (event.text or "").strip()
        if not text:
            return None
        reply_text = str(event.metadata.get("reply_to_text") or "").strip()

        # Fastest and safest UX: reply `стоп` to the actual bot reminder.
        if event.event_type is EventType.REPLY_TO_BOT and _STOP_REPLY_RE.search(text):
            cancelled = self.reminder_repo.cancel_reminder_from_bot_reply(
                scope_id=event.scope_id,
                reply_to_message_id=event.reply_to_message_id,
            )
            return GroupReminderAction(
                status="cancelled" if cancelled else "not_cancelled",
                cancelled_count=cancelled,
                reason="reply_to_reminder" if cancelled else "no_active_reminder_for_reply",
            )

        # Explicit stop commands work group-wide. A target narrows the kill
        # switch; without a target all active group reminders are stopped.
        if _CANCEL_REMINDER_RE.search(text):
            target_username = self._target_username(text, reply_text)
            cancelled = self.reminder_repo.cancel_active_group_reminders(
                scope_id=event.scope_id,
                target_username=target_username,
            )
            return GroupReminderAction(
                status="cancelled" if cancelled else "not_cancelled",
                target_username=target_username,
                cancelled_count=cancelled,
                reason=None if cancelled else "no_active_reminders",
            )

        timezone_name = self._timezone(text)
        pending = None
        clarification_timezone = None
        if event.event_type is EventType.REPLY_TO_BOT and event.actor_user_id:
            clarification_timezone = self._clarification_timezone(text)
            if clarification_timezone:
                pending = self.reminder_repo.get_pending_calendar_intent(
                    scope_id=event.scope_id,
                    actor_user_id=event.actor_user_id,
                    message_thread_id=event.metadata.get("message_thread_id"),
                    max_age_hours=24,
                )
        if pending:
            spec = dict(pending["payload"])
            return self._persist_calendar(
                event,
                now=now,
                spec=spec,
                timezone_name=clarification_timezone,
                source_event_id=pending["source_event_id"],
                pending_id=pending["id"],
            )

        if not _REMINDER_INTENT_RE.search(text):
            return None

        target_username = self._target_username(text, reply_text)
        stop_on_reply = bool(
            re.search(r"\bпока\b.*\bне\s+ответ", text, flags=re.IGNORECASE)
            or re.search(r"\bдо\s+ответ", text, flags=re.IGNORECASE)
        )
        if stop_on_reply and not target_username:
            return GroupReminderAction(status="not_scheduled", reason="target_required_for_stop_on_reply")

        calendar = self._calendar_spec(text, now)
        if calendar is not None:
            calendar = dict(calendar)
            calendar["source_message_id"] = event.message_id
            calendar["message_thread_id"] = event.metadata.get("message_thread_id")
            calendar["reference_at"] = event.occurred_at.isoformat()
            calendar["target_username"] = target_username
            calendar["stop_on_reply"] = stop_on_reply
            if not timezone_name:
                if event.actor_user_id:
                    self.reminder_repo.save_pending_calendar_intent(
                        scope_id=event.scope_id,
                        actor_user_id=event.actor_user_id,
                        source_event_id=event.event_id,
                        payload=calendar,
                    )
                return GroupReminderAction(status="not_scheduled", reason="timezone_required")
            return self._persist_calendar(
                event,
                now=now,
                spec=calendar,
                timezone_name=timezone_name,
                source_event_id=event.event_id,
            )

        interval_seconds = self._recurring_interval_seconds(text)
        one_shot_seconds = None if interval_seconds is not None else self._one_shot_delay_seconds(text)
        if interval_seconds is None and one_shot_seconds is None:
            return GroupReminderAction(status="not_scheduled", reason="unsupported_time_expression")

        delay_seconds = interval_seconds if interval_seconds is not None else one_shot_seconds
        assert delay_seconds is not None
        due_at = now + timedelta(seconds=delay_seconds)
        recurrence_rule = f"interval:{interval_seconds}" if interval_seconds is not None else None

        subject = reply_text or text
        subject = " ".join(subject.split())[:700]
        target_label = f"@{target_username}" if target_username else "участникам чата"
        reminder_text = (
            f"Сделай короткое напоминание {target_label} в характере НеНоя. "
            f"Не объясняй механику таймера. Контекст договорённости: {subject}"
        )
        payload = {
            "text": reminder_text,
            "actor_user_id": event.actor_user_id,
            "target_username": target_username,
            "stop_on_reply": stop_on_reply,
            "message_thread_id": event.metadata.get("message_thread_id"),
            "source_event_id": event.event_id,
            "source_message_id": event.message_id,
            "reminder_context": subject,
            "fire_count": 0,
            "max_occurrences": _MAX_GROUP_REMINDER_OCCURRENCES if interval_seconds is not None else 1,
        }
        record = self.reminder_repo.create(
            scope_type=ScopeType.GROUP,
            scope_id=event.scope_id,
            due_at=due_at,
            recurrence_rule=recurrence_rule,
            payload=payload,
            source_event_id=event.event_id,
        )
        already = bool(getattr(record, "already_existing", False))
        return GroupReminderAction(
            status="already_scheduled" if already else "scheduled",
            reminder_id=record.id,
            recurring=interval_seconds is not None,
            interval_seconds=interval_seconds,
            target_username=target_username,
            due_at=record.due_at,
            stop_on_reply=stop_on_reply,
            recurrence_rule=getattr(record, "recurrence_rule", recurrence_rule),
        )

    def _persist_calendar(self, event: EventEnvelope, *, now: datetime, spec: dict[str, Any],
                          timezone_name: str, source_event_id: str,
                          pending_id: int | None = None) -> GroupReminderAction:
        timezone_name = validate_timezone(timezone_name)
        one_shot_day = spec.get("one_shot_day")
        if one_shot_day is not None:
            reference_at = datetime.fromisoformat(str(spec.get("reference_at") or now.isoformat()))
            if reference_at.tzinfo is None or reference_at.utcoffset() is None:
                return GroupReminderAction(status="not_scheduled", reason="invalid_calendar_reference")
            local_today = reference_at.astimezone(ZoneInfo(timezone_name)).date()
            due_at = resolve_local(local_today + timedelta(days=int(one_shot_day)),
                                   int(spec["hour"]), int(spec["minute"]), timezone_name)
            if due_at <= now:
                return GroupReminderAction(status="not_scheduled", reason="calendar_time_in_past")
            rule = None
            recurring = False
        else:
            schedule = CalendarSchedule(spec["frequency"], int(spec["hour"]), int(spec["minute"]),
                                        timezone_name, spec.get("weekday"))
            rule = schedule.encode()
            due_at = next_calendar_occurrence(schedule, now)
            recurring = True
        subject = " ".join(str(spec.get("subject") or event.text or "").split())[:700]
        target_username = str(spec.get("target_username") or "").strip().lstrip("@") or None
        stop_on_reply = bool(spec.get("stop_on_reply"))
        target_label = f"@{target_username}" if target_username else "участникам чата"
        payload = {"text": f"Сделай короткое напоминание {target_label} в характере НеНоя. Контекст договорённости: {subject}",
                   "actor_user_id": event.actor_user_id, "source_event_id": source_event_id,
                   "source_message_id": spec.get("source_message_id") or event.message_id,
                   "message_thread_id": spec.get("message_thread_id"),
                   "target_username": target_username,
                   "stop_on_reply": stop_on_reply,
                   "reminder_context": subject,
                   "timezone": timezone_name, "fire_count": 0}
        if recurring:
            payload["recurrence_policy"] = "until_cancelled"
        else:
            payload["max_occurrences"] = 1
        record = self.reminder_repo.create(scope_type=ScopeType.GROUP, scope_id=event.scope_id,
                                           due_at=due_at, recurrence_rule=rule, payload=payload,
                                           source_event_id=source_event_id)
        if pending_id is not None:
            self.reminder_repo.complete_pending_calendar_intent(pending_id)
        already = bool(getattr(record, "already_existing", False))
        return GroupReminderAction(status="already_scheduled" if already else "scheduled",
                                   reminder_id=record.id, recurring=recurring, due_at=record.due_at,
                                   target_username=target_username, stop_on_reply=stop_on_reply,
                                   timezone=timezone_name, recurrence_rule=record.recurrence_rule)

    @classmethod
    def _timezone(cls, text: str) -> str | None:
        lowered = text.lower()
        candidates: list[str] = []
        for alias, name in _TZ_ALIASES.items():
            if alias in lowered:
                candidates.append(name)
        if _MSK_RE.search(text):
            candidates.append("Europe/Moscow")

        invalid_explicit = False
        for match in _TIMEZONE_TOKEN_RE.finditer(text):
            token = match.group(1)
            if token != "UTC" and "/" not in token:
                continue
            try:
                candidates.append(validate_timezone(token))
            except ValueError:
                invalid_explicit = True

        if invalid_explicit:
            return None
        distinct = set(candidates)
        if len(distinct) != 1:
            return None
        return next(iter(distinct))

    @classmethod
    def _clarification_timezone(cls, text: str) -> str | None:
        compact = " ".join(text.strip().split()).strip(" .!?…")
        lowered = compact.lower()
        if lowered == "по московскому времени" or _MSK_RE.fullmatch(compact):
            return "Europe/Moscow"

        token_text = compact
        if lowered.startswith("по "):
            token_text = compact[3:].strip()
        if token_text == "UTC" or _TIMEZONE_TOKEN_RE.fullmatch(token_text):
            return cls._timezone(token_text)
        return None

    @staticmethod
    def _calendar_spec(text: str, now: datetime) -> dict[str, Any] | None:
        if len(_CLOCK_ATTEMPT_RE.findall(text)) != 1:
            return None
        match = _CALENDAR_RE.search(text)
        if not match:
            return None
        selected_start, selected_end = match.span("kind")
        for kind_match in _CALENDAR_KIND_RE.finditer(text):
            if kind_match.span() == (selected_start, selected_end):
                continue
            if kind_match.end() <= selected_start:
                bridge = text[kind_match.end():selected_start]
            elif kind_match.start() >= match.end():
                bridge = text[match.end():kind_match.start()]
            else:
                bridge = text[selected_end:kind_match.start()]
            if _CALENDAR_ALT_BRIDGE_RE.fullmatch(bridge):
                return None
        kind = match.group("kind").lower()
        hour = int(match.group("hour"))
        period = (match.group("period") or "").lower()
        if period:
            if period != "утра" or hour > 11:
                return None
        if "утро" in kind and hour > 11:
            return None
        spec: dict[str, Any] = {"hour": hour,
                                "minute": int(match.group("minute") or 0), "subject": text}
        if kind == "сегодня": spec["one_shot_day"] = 0
        elif kind == "завтра": spec["one_shot_day"] = 1
        elif kind == "по будням": spec["frequency"] = "weekdays"
        elif "пятниц" in kind: spec.update(frequency="weekly", weekday=4)
        else: spec["frequency"] = "daily"
        return spec

    @staticmethod
    def _target_username(text: str, reply_text: str) -> str | None:
        for source in (text, reply_text):
            match = _USERNAME_RE.search(source)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _recurring_interval_seconds(text: str) -> int | None:
        if _EVERY_HALF_HOUR_RE.search(text):
            return 30 * 60
        match = _EVERY_INTERVAL_RE.search(text)
        if not match:
            return None
        amount = int(match.group(1) or 1)
        unit = match.group(2).lower()
        seconds = amount * (3600 if unit.startswith("час") else 60)
        if _MIN_RECURRING_INTERVAL_SECONDS <= seconds <= 31 * 24 * 60 * 60:
            return seconds
        return None

    @staticmethod
    def _one_shot_delay_seconds(text: str) -> int | None:
        match = _AFTER_INTERVAL_RE.search(text)
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2).lower()
        seconds = amount * (3600 if unit.startswith("час") else 60)
        if 60 <= seconds <= 31 * 24 * 60 * 60:
            return seconds
        return None
