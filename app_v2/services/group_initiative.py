from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app_v2.domain.connectors import ConnectorConfig
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.group_context_repo import GroupContext


_IGNORED_TYPES = ("ignored", "no_engagement")
_NEGATIVE_TYPES = (
    "negative", "dislike", "mute", "shut_up", "report",
    "reaction_negative", "explicit_negative",
)
_POSITIVE_TYPES = ("positive", "like", "reaction_positive", "helpful")
_MUTE_PHRASES = (
    "заткнись",
    "замолчи",
    "помолчи",
    "не лезь",
    "тихо, бот",
    "тихо бот",
)
_BOT_ADDRESSED_GROUP_TYPES = {
    EventType.DIRECT_MENTION,
    EventType.REPLY_TO_BOT,
    EventType.REPLY_TO_BOT_MESSAGE,
    EventType.NEGATIVE_FEEDBACK,
    EventType.MUTE_REQUEST,
}


def is_addressed_to_bot(event: EventEnvelope) -> bool:
    """Return whether text/control semantics are explicitly scoped to НеНой.

    Personal messages are inherently addressed to the bot. In groups we trust
    only normalized direct/reply events and explicit feedback/control event
    types; ordinary group text (including replies between people) must not be
    reinterpreted as a bot command merely because it contains a trigger phrase.
    """

    if event.scope_type is ScopeType.PERSONAL:
        return event.event_type not in {EventType.REACTION_ADDED, EventType.REACTION_REMOVED}
    return event.event_type in _BOT_ADDRESSED_GROUP_TYPES


def _int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _float(value: Any, default: float, *, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


@dataclass(frozen=True)
class GroupInitiativeSnapshot:
    group_muted: bool
    silence_requested: bool
    cooldown_active: bool
    effective_cooldown_minutes: int
    unsolicited_today: int
    soft_daily_limit: int
    hard_daily_limit: int
    initiative_level: int
    ignored_unsolicited_recent: int
    negative_feedback_recent: int
    positive_feedback_recent: int
    bot_spoke_recently: bool
    bot_share_blocked: bool
    bot_share: float
    metadata: dict[str, Any] = field(default_factory=dict)


class GroupInitiativeService:
    """Calculate adaptive unsolicited policy from durable Group history.

    Negative/ignored signals have deliberately larger and faster effects than
    positive signals. Explicit replies are still handled by Dispatcher hard
    intents and are not blocked by these unsolicited guardrails.
    """

    def __init__(
        self,
        repo: Any,
        connector_resolver: Any | None = None,
    ) -> None:
        self.repo = repo
        self.connector_resolver = connector_resolver

    @staticmethod
    def is_silence_request(text: str | None) -> bool:
        value = (text or "").strip().lower()
        return bool(value and any(phrase in value for phrase in _MUTE_PHRASES))

    def evaluate(
        self,
        *,
        event: EventEnvelope,
        group_context: GroupContext,
        now: datetime,
        connector_config: ConnectorConfig | None = None,
    ) -> GroupInitiativeSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        if connector_config is None and self.connector_resolver is not None:
            connector_config = self.connector_resolver.resolve(group_context)

        if connector_config is not None:
            behavior = connector_config.behavior
            base_cooldown = behavior.cooldown_minutes
            soft_limit = behavior.soft_daily_limit
            hard_limit = max(behavior.hard_daily_limit, max(soft_limit, 1))
            base_initiative = behavior.initiative
            mute_minutes = behavior.mute_minutes
            max_bot_share = behavior.bot_share_max
            share_window_minutes = behavior.bot_share_window_minutes
            min_messages_for_share = behavior.bot_share_min_messages
        else:
            profile = dict(group_context.profile or {})
            base_cooldown = _int(profile.get("cooldown_minutes"), 12, low=1, high=1440)
            soft_limit = _int(profile.get("soft_daily_limit"), 6, low=0, high=100)
            hard_limit = _int(profile.get("hard_daily_limit"), 10, low=max(soft_limit, 1), high=200)
            base_initiative = _int(profile.get("initiative"), 6, low=0, high=10)
            mute_minutes = _int(profile.get("mute_minutes"), 120, low=1, high=10080)
            max_bot_share = _float(profile.get("bot_share_max"), 0.10, low=0.01, high=1.0)
            share_window_minutes = _int(profile.get("bot_share_window_minutes"), 60, low=5, high=1440)
            min_messages_for_share = _int(profile.get("bot_share_min_messages"), 10, low=1, high=1000)

        silence_requested = is_addressed_to_bot(event) and (
            event.event_type is EventType.MUTE_REQUEST
            or self.is_silence_request(event.text)
        )
        silent_until = group_context.silent_until
        if silence_requested:
            requested_until = now + timedelta(minutes=mute_minutes)
            self.repo.set_silent_until(event.scope_id, requested_until)
            if silent_until is None or requested_until > silent_until:
                silent_until = requested_until

        group_muted = bool(silent_until and silent_until > now)
        feedback_since = now - timedelta(hours=24)
        ignored = self.repo.count_feedback_since(event.scope_id, _IGNORED_TYPES, feedback_since)
        negative = self.repo.count_feedback_since(event.scope_id, _NEGATIVE_TYPES, feedback_since)
        positive = self.repo.count_feedback_since(event.scope_id, _POSITIVE_TYPES, feedback_since)

        multiplier = 1.0
        if ignored >= 2:
            multiplier *= 1.5
        if negative >= 1:
            multiplier *= 1.25
        effective_cooldown = min(1440, max(base_cooldown, math.ceil(base_cooldown * multiplier)))

        utc_now = now.astimezone(timezone.utc)
        day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        unsolicited_today = self.repo.count_unsolicited_since(event.scope_id, day_start)
        last_unsolicited = self.repo.last_unsolicited_at(event.scope_id)
        cooldown_active = bool(
            last_unsolicited
            and last_unsolicited > now - timedelta(minutes=effective_cooldown)
        )

        positive_bonus = 1 if positive >= 3 else 0
        negative_penalty = min(4, negative * 2)
        ignore_penalty = 1 if ignored >= 2 else 0
        initiative = max(0, min(10, base_initiative + positive_bonus - negative_penalty - ignore_penalty))

        scene_since = now - timedelta(minutes=share_window_minutes)
        group_messages = self.repo.count_messages_since(event.scope_id, scene_since)
        recent_unsolicited = self.repo.count_unsolicited_since(event.scope_id, scene_since)
        bot_share = recent_unsolicited / max(group_messages, 1)
        bot_share_blocked = group_messages >= min_messages_for_share and bot_share >= max_bot_share
        cooldown_active = cooldown_active or bot_share_blocked
        bot_spoke_recently = bool(last_unsolicited and last_unsolicited > scene_since)

        return GroupInitiativeSnapshot(
            group_muted=group_muted,
            silence_requested=silence_requested,
            cooldown_active=cooldown_active,
            effective_cooldown_minutes=effective_cooldown,
            unsolicited_today=unsolicited_today,
            soft_daily_limit=soft_limit,
            hard_daily_limit=hard_limit,
            initiative_level=initiative,
            ignored_unsolicited_recent=min(ignored, 2),
            negative_feedback_recent=negative,
            positive_feedback_recent=positive,
            bot_spoke_recently=bot_spoke_recently,
            bot_share_blocked=bot_share_blocked,
            bot_share=bot_share,
            metadata={
                "base_cooldown_minutes": base_cooldown,
                "effective_cooldown_minutes": effective_cooldown,
                "bot_share": round(bot_share, 4),
                "bot_share_max": max_bot_share,
                "positive_bonus": positive_bonus,
                "negative_penalty": negative_penalty,
                "ignore_penalty": ignore_penalty,
            },
        )
