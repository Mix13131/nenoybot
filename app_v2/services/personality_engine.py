from __future__ import annotations

from collections.abc import Mapping

from app_v2.domain.enums import ResponseMode, ScopeType
from app_v2.domain.events import SceneAnalysis
from app_v2.domain.personality import PersonalityState


_FIELDS = (
    "directness", "brevity", "warmth", "pressure", "humor", "sarcasm",
    "roast", "profanity_level", "profanity_frequency", "initiative",
    "callback", "challenge", "care", "playfulness", "sensitivity",
)

PERSONAL_DEFAULT: dict[str, int] = {
    "directness": 8,
    "brevity": 8,
    "warmth": 7,
    "pressure": 6,
    "humor": 5,
    "sarcasm": 4,
    "roast": 3,
    "profanity_level": 4,
    "profanity_frequency": 3,
    "initiative": 7,
    "callback": 8,
    "challenge": 8,
    "care": 8,
    "playfulness": 5,
    "sensitivity": 8,
}

FRIENDS_GROUP_DEFAULT: dict[str, int] = {
    "directness": 9,
    "brevity": 8,
    "warmth": 3,
    "pressure": 4,
    "humor": 10,
    "sarcasm": 10,
    "roast": 9,
    "profanity_level": 8,
    "profanity_frequency": 5,
    "initiative": 6,
    "callback": 10,
    "challenge": 8,
    "care": 3,
    "playfulness": 10,
    "sensitivity": 7,
}

_MODE_MODIFIERS: dict[ResponseMode, dict[str, int]] = {
    ResponseMode.COACH: {"pressure": 2, "challenge": 2, "care": -1, "brevity": 1},
    ResponseMode.CARE: {"pressure": -5, "challenge": -4, "warmth": 3, "care": 3, "sarcasm": -2},
    ResponseMode.MIRROR: {"callback": 2, "challenge": 1, "directness": 1, "initiative": 1},
    ResponseMode.GROUP_DIRECT_REPLY: {
        "brevity": 1,
        "humor": 1,
        "sarcasm": 1,
        "roast": 1,
        "playfulness": 1,
    },
    ResponseMode.GROUP_BANTER: {"playfulness": 1, "sarcasm": 1, "roast": 1},
    ResponseMode.GROUP_ROAST: {
        "roast": 2,
        "sarcasm": 1,
        "callback": 2,
        "playfulness": 1,
        "brevity": 2,
    },
    ResponseMode.GROUP_CALLBACK: {"callback": 2, "brevity": 2, "sarcasm": 1, "roast": 1},
    ResponseMode.GROUP_HELP: {
        "brevity": 1,
        "humor": 1,
        "sarcasm": 1,
        "roast": 1,
        "playfulness": 1,
    },
    ResponseMode.GROUP_ORGANIZER: {"brevity": 1, "directness": 1, "sarcasm": 1},
    ResponseMode.GROUP_ARBITER: {"brevity": 0, "warmth": 1, "sensitivity": 1},
}


def _clamp(value: int | float) -> int:
    return max(0, min(10, int(round(value))))


def _apply_values(target: dict[str, int], values: Mapping[str, int | float]) -> None:
    for key, value in values.items():
        if key in target:
            target[key] = _clamp(value)


def _apply_modifiers(target: dict[str, int], modifiers: Mapping[str, int | float]) -> None:
    for key, delta in modifiers.items():
        if key in target:
            target[key] = _clamp(target[key] + delta)


class PersonalityEngine:
    """Pure deterministic personality calculator; no model calls and no I/O."""

    def build(
        self,
        *,
        scope_type: ScopeType,
        mode: ResponseMode,
        scene: SceneAnalysis | None = None,
        context_profile: Mapping[str, int | float] | None = None,
        participant_adaptation: Mapping[str, int | float] | None = None,
        temporary_overrides: Mapping[str, int | float] | None = None,
    ) -> PersonalityState:
        base = dict(PERSONAL_DEFAULT if scope_type is ScopeType.PERSONAL else FRIENDS_GROUP_DEFAULT)
        group_profanity_ceiling = (
            int(context_profile.get("profanity_level", base["profanity_level"]))
            if scope_type is ScopeType.GROUP and context_profile
            else base["profanity_level"]
        )
        group_frequency_ceiling = (
            int(context_profile.get("profanity_frequency", base["profanity_frequency"]))
            if scope_type is ScopeType.GROUP and context_profile
            else base["profanity_frequency"]
        )

        if context_profile:
            _apply_values(base, context_profile)
        if participant_adaptation:
            _apply_modifiers(base, participant_adaptation)

        _apply_modifiers(base, _MODE_MODIFIERS.get(mode, {}))

        if temporary_overrides:
            _apply_values(base, temporary_overrides)

        if scene is not None:
            self._apply_scene(base, scene)

        if scope_type is ScopeType.GROUP:
            base["profanity_level"] = min(base["profanity_level"], _clamp(group_profanity_ceiling))
            base["profanity_frequency"] = min(base["profanity_frequency"], _clamp(group_frequency_ceiling))

        for key in _FIELDS:
            base[key] = _clamp(base[key])

        return PersonalityState(mode=mode, **base)

    @staticmethod
    def _apply_scene(values: dict[str, int], scene: SceneAnalysis) -> None:
        serious = scene.seriousness_score >= 0.75
        conflict = scene.conflict_score >= 0.75
        sensitive = scene.sensitivity_score >= 0.75

        if serious or conflict:
            values["roast"] = min(values["roast"], 2)
            values["humor"] = min(values["humor"], 2)
            values["sarcasm"] = min(values["sarcasm"], 3)
            values["profanity_level"] = min(values["profanity_level"], 4)
            values["pressure"] = min(values["pressure"], 4)
            values["sensitivity"] = 10

        if sensitive:
            values["roast"] = min(values["roast"], 1)
            values["pressure"] = min(values["pressure"], 3)
            values["care"] = max(values["care"], 8)
            values["sensitivity"] = 10
