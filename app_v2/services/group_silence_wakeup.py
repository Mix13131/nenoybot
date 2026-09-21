from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_v2.domain.connectors import ConnectorConfig
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


def silence_wakeup_window_open(
    profile: dict[str, Any],
    *,
    now: datetime,
    connector_config: ConnectorConfig | None = None,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    if connector_config is not None:
        behavior = connector_config.behavior
        enabled = behavior.silence_wakeup_enabled
        unsolicited_enabled = behavior.unsolicited_enabled
        timezone_name = str(behavior.timezone or "").strip()
        start_hour = behavior.silence_wakeup_start_hour
        end_hour = behavior.silence_wakeup_end_hour
    else:
        enabled = _bool(profile.get("silence_wakeup_enabled"), False)
        unsolicited_enabled = _bool(profile.get("unsolicited_enabled"), False)
        timezone_name = str(profile.get("timezone") or "").strip()
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

    if not enabled or not unsolicited_enabled or not timezone_name:
        return False
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return False
    if start_hour >= end_hour:
        return False

    local_now = now.astimezone(local_tz)
    return start_hour <= local_now.hour < end_hour


class GroupSilenceWakeupService:
    """Create one durable proactive event when an approved group goes quiet."""

    def __init__(
        self,
        *,
        repo: Any,
        group_context_repo: Any,
        initiative_service: Any,
        connector_resolver: Any | None = None,
    ) -> None:
        self.repo = repo
        self.group_context_repo = group_context_repo
        self.initiative_service = initiative_service
        self.connector_resolver = connector_resolver

    def run_once(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        for candidate in self.repo.list_candidates(limit=50):
            group_context = self.group_context_repo.load(candidate.scope_id, None)
            if (
                group_context is None
                or not group_context.is_whitelisted
                or not group_context.is_active
            ):
                continue

            connector_config = None
            if self.connector_resolver is not None:
                try:
                    connector_config = self.connector_resolver.resolve(group_context)
                except Exception:
                    continue

            profile = dict(candidate.profile or {})
            if not silence_wakeup_window_open(
                profile,
                now=current,
                connector_config=connector_config,
            ):
                continue

            if connector_config is not None:
                behavior = connector_config.behavior
                timezone_name = str(behavior.timezone or "").strip()
                threshold_minutes = behavior.silence_wakeup_after_minutes
                daily_limit = behavior.silence_wakeup_daily_limit
            else:
                timezone_name = str(profile.get("timezone") or "").strip()
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
            local_tz = ZoneInfo(timezone_name)
            if daily_limit == 0:
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
            local_day_start = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            if self.repo.count_successful_since(
                candidate.scope_id,
                local_day_start,
            ) >= daily_limit:
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
            if connector_config is not None:
                snapshot = self.initiative_service.evaluate(
                    event=probe_event,
                    group_context=group_context,
                    now=current,
                    connector_config=connector_config,
                )
            else:
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
