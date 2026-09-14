from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.enums import ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext
from app_v2.services.dispatcher import DispatcherPolicyState


_CALLBACK_TYPES = {"running_joke", "pattern", "contradiction", "commitment", "quote"}


def _int(value: Any, default: int, *, low: int = 0, high: int = 10) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


@dataclass(frozen=True)
class GroupBehaviorPlan:
    scene: SceneAnalysis
    state: DispatcherPolicyState
    memory_usage: str
    callback_fatigue_minutes: int
    callback_memory_ids: tuple[str, ...]
    context_profile: dict[str, Any]
    participant_adaptation: dict[str, int | float]


class GroupBehaviorEngine:
    """Build deterministic Group behavior state from profile, memory and history."""

    def __init__(self, retrieval_engine: Any, initiative_service: Any | None = None) -> None:
        self.retrieval_engine = retrieval_engine
        self.initiative_service = initiative_service

    def plan(
        self,
        *,
        event: EventEnvelope,
        group_context: GroupContext,
        scene: SceneAnalysis,
        now: datetime,
    ) -> GroupBehaviorPlan:
        if event.scope_type is not ScopeType.GROUP:
            raise ValueError("GroupBehaviorEngine requires group scope")

        profile = dict(group_context.profile or {})
        participant = dict(group_context.participant.profile or {})
        adaptation_raw = participant.get("personality_modifiers")
        adaptation = dict(adaptation_raw) if isinstance(adaptation_raw, dict) else {}

        unsolicited_enabled = _bool(profile.get("unsolicited_enabled"), False)
        roast_level = _int(profile.get("roast"), 9)
        callback_level = _int(profile.get("callback"), 10)
        initiative = _int(profile.get("initiative"), 6)
        roast_tolerance = _int(participant.get("roast_tolerance"), 7)
        fatigue = _int(profile.get("callback_fatigue_minutes"), 180, low=0, high=1440)

        dynamic = None
        if self.initiative_service is not None:
            dynamic = self.initiative_service.evaluate(
                event=event,
                group_context=group_context,
                now=now,
            )

        silence_requested = bool(dynamic and dynamic.silence_requested)
        effective_scene = scene
        if silence_requested:
            effective_scene = scene.model_copy(
                update={
                    "command_intent": "mute",
                    "roast_opportunity": 0.0,
                    "callback_opportunity": 0.0,
                }
            )

        safe_scene = (
            effective_scene.seriousness_score < 0.75
            and effective_scene.conflict_score < 0.75
            and effective_scene.sensitivity_score < 0.75
        )
        subject_keys = [f"user:{event.actor_user_id}"] if event.actor_user_id else []

        probe = []
        if not silence_requested and callback_level > 0 and roast_tolerance >= 3:
            probe = self.retrieval_engine.retrieve(
                ScopeType.GROUP,
                event.scope_id,
                usage="callback",
                subject_keys=subject_keys,
                callback_fatigue_minutes=fatigue,
                limit=4,
            )

        callback_cards = [
            item for item in probe
            if item.card.memory_type in _CALLBACK_TYPES and item.card.confidence >= 0.75
        ]
        callback_ids = tuple(item.card.id for item in callback_cards)
        running_joke_fit = any(item.card.memory_type == "running_joke" for item in callback_cards)
        broken_commitment = any(
            item.card.memory_type == "commitment"
            and str(item.card.payload.get("status", "")).lower() in {"broken", "overdue", "missed"}
            for item in callback_cards
        )

        allow_callbacks = (
            not silence_requested
            and callback_level > 0
            and roast_tolerance >= 3
            and bool(callback_cards)
        )
        allow_roast = (
            not silence_requested
            and roast_level > 0
            and roast_tolerance >= 5
            and safe_scene
        )

        if allow_callbacks:
            changes: dict[str, float] = {
                "callback_opportunity": max(effective_scene.callback_opportunity, 0.82)
            }
            if running_joke_fit and allow_roast:
                changes["roast_opportunity"] = max(effective_scene.roast_opportunity, 0.80)
            effective_scene = effective_scene.model_copy(update=changes)

        if dynamic is not None:
            muted = dynamic.group_muted
            cooldown_active = (not unsolicited_enabled) or dynamic.cooldown_active
            initiative = dynamic.initiative_level
            unsolicited_today = dynamic.unsolicited_today
            soft_daily_limit = dynamic.soft_daily_limit
            hard_daily_limit = dynamic.hard_daily_limit
            bot_spoke_recently = dynamic.bot_spoke_recently
            ignored_recent = dynamic.ignored_unsolicited_recent
            dynamic_metadata = {
                **dynamic.metadata,
                "silence_requested_control": dynamic.silence_requested,
                "bot_share_blocked": dynamic.bot_share_blocked,
                "positive_feedback_recent": dynamic.positive_feedback_recent,
                "negative_feedback_recent": dynamic.negative_feedback_recent,
            }
        else:
            muted = bool(group_context.silent_until and group_context.silent_until > now)
            cooldown_active = not unsolicited_enabled
            unsolicited_today = 0
            soft_daily_limit = 6
            hard_daily_limit = 10
            bot_spoke_recently = False
            ignored_recent = 0
            dynamic_metadata = {}

        state = DispatcherPolicyState(
            group_muted=muted,
            cooldown_active=cooldown_active,
            unsolicited_today=unsolicited_today,
            soft_daily_limit=soft_daily_limit,
            hard_daily_limit=hard_daily_limit,
            initiative_level=initiative,
            bot_spoke_recently=bot_spoke_recently,
            ignored_unsolicited_recent=ignored_recent,
            running_joke_fit=running_joke_fit,
            broken_commitment_relevant=broken_commitment,
            allow_roast=allow_roast,
            allow_callbacks=allow_callbacks,
            metadata={
                "group_profile": profile.get("profile", "friends"),
                "unsolicited_enabled": unsolicited_enabled,
                "roast_tolerance": roast_tolerance,
                "callback_probe_ids": list(callback_ids),
                **dynamic_metadata,
            },
        )

        return GroupBehaviorPlan(
            scene=effective_scene,
            state=state,
            memory_usage="callback" if allow_callbacks else "assist",
            callback_fatigue_minutes=fatigue,
            callback_memory_ids=callback_ids,
            context_profile=profile,
            participant_adaptation=adaptation,
        )
