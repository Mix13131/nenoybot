from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope


_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,32})")
_REMINDER_INTENT_RE = re.compile(r"\b(?:напоминай|напомни)\b", flags=re.IGNORECASE)
_EVERY_HALF_HOUR_RE = re.compile(r"\bкажд\w*\s+пол\s*час", flags=re.IGNORECASE)
_EVERY_INTERVAL_RE = re.compile(
    r"\bкажд\w*\s+(?:(\d{1,3})\s*)?(минут\w*|час\w*)",
    flags=re.IGNORECASE,
)
_AFTER_INTERVAL_RE = re.compile(
    r"\bчерез\s+(\d{1,3})\s*(минут\w*|час\w*)",
    flags=re.IGNORECASE,
)


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
        }


class GroupReminderService:
    """Small deterministic action layer for reminders requested inside a group.

    MVP intentionally handles simple relative timing only. It does not try to be
    a natural-language calendar; the important product behavior is that НеНой
    can actually schedule a nudge in the same chat/topic instead of merely
    joking about it.
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
        if not text or not _REMINDER_INTENT_RE.search(text):
            return None

        interval_seconds = self._recurring_interval_seconds(text)
        one_shot_seconds = None if interval_seconds is not None else self._one_shot_delay_seconds(text)
        if interval_seconds is None and one_shot_seconds is None:
            return GroupReminderAction(status="not_scheduled", reason="unsupported_time_expression")

        reply_text = str(event.metadata.get("reply_to_text") or "").strip()
        target_username = self._target_username(text, reply_text)
        stop_on_reply = bool(
            re.search(r"\bпока\b.*\bне\s+ответ", text, flags=re.IGNORECASE)
            or re.search(r"\bдо\s+ответ", text, flags=re.IGNORECASE)
        )
        if stop_on_reply and not target_username:
            return GroupReminderAction(status="not_scheduled", reason="target_required_for_stop_on_reply")

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
        }
        record = self.reminder_repo.create(
            scope_type=ScopeType.GROUP,
            scope_id=event.scope_id,
            due_at=due_at,
            recurrence_rule=recurrence_rule,
            payload=payload,
        )
        return GroupReminderAction(
            status="scheduled",
            reminder_id=record.id,
            recurring=interval_seconds is not None,
            interval_seconds=interval_seconds,
            target_username=target_username,
            due_at=record.due_at,
            stop_on_reply=stop_on_reply,
        )

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
        if 60 <= seconds <= 31 * 24 * 60 * 60:
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
