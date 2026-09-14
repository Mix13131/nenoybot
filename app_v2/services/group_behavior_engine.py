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
    """Build deterministic Group behavior state from profile + scoped memory.

    The engine never generates text. It can only strengthen an already grounded
    callback/roast opportunity when current Group-scope memory survives fatigue
    and policy gates.
    """

    def __init__(self, retrieval_engine: Any) -> None:
        self.retrieval_engine = retrieval_engine

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

        safe_scene = (
            scene.seriousness_score < 0.75
            and scene.conflict_score < 0.75
            and scene.sensitivity_score < 0.75
        )
        subject_keys = [f"user:{event.actor_user_id}"] if event.actor_user_id else []

        probe = []
        if callback_level > 0 and roast_tolerance >= 3:
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

        allow_callbacks = callback_level > 0 and roast_tolerance >= 3 and bool(callback_cards)
        allow_roast = roast_level > 0 and roast_tolerance >= 5 and safe_scene

        effective_scene = scene
        if allow_callbacks:
            changes: dict[str, float] = {
                "callback_opportunity": max(scene.callback_opportunity, 0.82)
            }
            # A fresh running joke is evidence-backed social material, not a
            # random quip. Let it also satisfy Roast Gate when the scene is safe.
            if running_joke_fit and allow_roast:
                changes["roast_opportunity"] = max(scene.roast_opportunity, 0.80)
            effective_scene = scene.model_copy(update=changes)

        muted = bool(group_context.silent_until and group_context.silent_until > now)
        state = DispatcherPolicyState(
            group_muted=muted,
            cooldown_active=not unsolicited_enabled,
            initiative_level=initiative,
            running_joke_fit=running_joke_fit,
            broken_commitment_relevant=broken_commitment,
            allow_roast=allow_roast,
            allow_callbacks=allow_callbacks,
            metadata={
                "group_profile": profile.get("profile", "friends"),
                "unsolicited_enabled": unsolicited_enabled,
                "roast_tolerance": roast_tolerance,
                "callback_probe_ids": list(callback_ids),
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
