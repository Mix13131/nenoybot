from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    return default


def _int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


class GroupSilenceWakeupService:
    """Create one durable proactive event when an approved group goes quiet."""

    def __init__(
        self,
        *,
        repo: Any,
        group_context_repo: Any,
        initiative_service: Any,
    ) -> None:
        self.repo = repo
        self.group_context_repo = group_context_repo
        self.initiative_service = initiative_service

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        for candidate in self.repo.list_candidates(limit=50):
            profile = dict(candidate.profile or {})
            if not _bool(profile.get("silence_wakeup_enabled"), False):
                continue
            if not _bool(profile.get("unsolicited_enabled"), False):
                continue

            timezone_name = str(profile.get("timezone") or "").strip()
            if not timezone_name:
                continue
            try:
                local_tz = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                continue

            threshold_minutes = _int(
                profile.get("silence_wakeup_after_minutes"),
                180,
                low=60,
                high=2880,
            )
            daily_limit = _int(
                profile.get("silence_wakeup_daily_limit"),
                1,
                low=0,
                high=5,
            )
            start_hour = _int(
                profile.get("silence_wakeup_start_hour"),
                10,
                low=0,
                high=23,
            )
            end_hour = _int(
                profile.get("silence_wakeup_end_hour"),
                22,
                low=1,
                high=24,
            )
            if start_hour >= end_hour or daily_limit == 0:
                continue

            silence_minutes = int(
                (current - candidate.last_human_message_at).total_seconds() // 60
            )
            if silence_minutes < threshold_minutes:
                continue
            if candidate.silent_until and candidate.silent_until > current:
                continue
            if (
                candidate.last_successful_wakeup_message_id
                == candidate.last_human_message_id
            ):
                # One successful wakeup per exact human silence episode.
                continue
            if candidate.last_attempt_message_id == candidate.last_human_message_id:
                # One attempt per exact episode even if later policy/generation
                # decides not to speak. A newer human message opens a new episode.
                continue

            local_now = current.astimezone(local_tz)
            if not (start_hour <= local_now.hour < end_hour):
                continue
            local_day_start = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            if self.repo.count_successful_since(
                candidate.scope_id,
                local_day_start,
            ) >= daily_limit:
                continue

            group_context = self.group_context_repo.load(candidate.scope_id, None)
            if (
                group_context is None
                or not group_context.is_whitelisted
                or not group_context.is_active
            ):
                continue

            probe_event = EventEnvelope(
                event_id=f"silence-probe:{candidate.scope_id}:{candidate.last_human_message_id}",
                event_type=EventType.GROUP_SILENCE_WAKEUP,
                occurred_at=current,
                scope_type=ScopeType.GROUP,
                scope_id=candidate.scope_id,
                actor_user_id=None,
                message_id=None,
                text=None,
                metadata={
                    "synthetic": True,
                    "silence_wakeup": True,
                    "silence_minutes": silence_minutes,
                    "last_human_excerpt": candidate.last_human_excerpt,
                },
            )
            snapshot = self.initiative_service.evaluate(
                event=probe_event,
                group_context=group_context,
                now=current,
            )
            if snapshot.group_muted or snapshot.cooldown_active:
                continue
            if snapshot.negative_feedback_recent > 0:
                continue
            if snapshot.unsolicited_today >= snapshot.soft_daily_limit:
                continue
            if snapshot.initiative_level <= 0:
                continue

            if self.repo.enqueue(
                candidate,
                now=current,
                silence_minutes=silence_minutes,
            ):
                return True
        return False
