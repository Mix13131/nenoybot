from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping


STYLE_VERSION = "group_style_v1"
STYLE_WINDOW_DAYS = 30
_STYLE_DIMENSIONS = frozenset(
    {"brevity", "humor", "roast", "warmth", "playfulness", "callback", "initiative"}
)
_POSITIVE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "banter_roast": ("humor", "roast", "playfulness"),
    "callback": ("callback", "humor"),
    "proactive": ("initiative",),
    "social_ack": ("brevity", "warmth", "playfulness"),
    # Ordinary direct/help replies are deliberately not reinforced by a generic
    # positive reaction. This prevents laughter on a complaint from teaching
    # the complained-about style.
    "direct": (),
}
_NEGATIVE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "banter_roast": ("humor", "roast", "playfulness"),
    "callback": ("callback", "humor"),
    "proactive": ("initiative",),
    "social_ack": ("warmth", "playfulness"),
    "direct": ("humor", "roast", "playfulness"),
}


@dataclass(frozen=True)
class StyleEvidence:
    intervention_id: int
    family: str
    valence: str
    actor_key: str
    occurred_at: datetime
    feedback_type: str


@dataclass(frozen=True)
class GroupStyleState:
    scope_id: str
    deltas: dict[str, int]
    effective_profile: dict[str, Any]
    evidence: dict[str, dict[str, int]]
    evaluated_at: datetime
    window_days: int = STYLE_WINDOW_DAYS
    version: str = STYLE_VERSION

    def as_metadata(self, base_profile: Mapping[str, Any]) -> dict[str, Any]:
        base_dimensions = {
            key: int(round(value))
            for key, value in base_profile.items()
            if key in _STYLE_DIMENSIONS and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        effective_dimensions = {
            key: int(round(value))
            for key, value in self.effective_profile.items()
            if key in _STYLE_DIMENSIONS and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        return {
            "version": self.version,
            "window_days": self.window_days,
            "evaluated_at": self.evaluated_at.isoformat(),
            "deltas": dict(self.deltas),
            "base": base_dimensions,
            "effective": effective_dimensions,
            "evidence": {family: dict(counts) for family, counts in self.evidence.items()},
        }


def _level(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(10, int(round(value))))


def apply_style_deltas(
    base_profile: Mapping[str, Any],
    deltas: Mapping[str, int | float],
) -> dict[str, Any]:
    result = dict(base_profile)
    for dimension, delta in deltas.items():
        if dimension not in _STYLE_DIMENSIONS:
            continue
        current = _level(result.get(dimension))
        if current is None:
            continue
        result[dimension] = max(0, min(10, current + int(round(delta))))
    return result


class GroupStyleService:
    """Derive a small reversible group style overlay from durable feedback."""

    def __init__(self, repo: Any, *, window_days: int = STYLE_WINDOW_DAYS) -> None:
        self.repo = repo
        self.window_days = max(1, min(int(window_days), 90))

    def evaluate(
        self,
        scope_id: str,
        *,
        base_profile: Mapping[str, Any],
        now: datetime,
    ) -> GroupStyleState:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        since = now - timedelta(days=self.window_days)
        rows = self.repo.recent_evidence(scope_id, since=since, until=now)

        positive_actors: dict[str, set[str]] = defaultdict(set)
        negative_actors: dict[str, set[str]] = defaultdict(set)
        positive_events: dict[str, int] = defaultdict(int)
        negative_events: dict[str, int] = defaultdict(int)

        for item in rows:
            if not isinstance(item, StyleEvidence):
                continue
            if item.occurred_at < since or item.occurred_at >= now:
                continue
            if not item.actor_key:
                continue
            if item.valence == "positive":
                positive_actors[item.family].add(item.actor_key)
                positive_events[item.family] += 1
            elif item.valence == "negative":
                negative_actors[item.family].add(item.actor_key)
                negative_events[item.family] += 1

        raw_deltas: dict[str, int] = defaultdict(int)
        evidence_summary: dict[str, dict[str, int]] = {}
        families = sorted(set(positive_actors) | set(negative_actors))
        for family in families:
            negatives = negative_actors[family]
            # An actor who supplied credible negative evidence is not also used
            # as independent positive support for the same family in this window.
            positives = positive_actors[family] - negatives

            positive_strength = 0
            if len(positives) >= 4:
                positive_strength = 2
            elif len(positives) >= 2:
                positive_strength = 1

            negative_strength = 0
            if len(negatives) >= 2:
                negative_strength = -3
            elif len(negatives) == 1:
                negative_strength = -2

            for dimension in _POSITIVE_DIMENSIONS.get(family, ()):
                raw_deltas[dimension] += positive_strength
            for dimension in _NEGATIVE_DIMENSIONS.get(family, ()):
                raw_deltas[dimension] += negative_strength

            evidence_summary[family] = {
                "positive_events": positive_events[family],
                "negative_events": negative_events[family],
                "positive_actors": len(positives),
                "negative_actors": len(negatives),
            }

        deltas = {
            dimension: max(-3, min(2, value))
            for dimension, value in raw_deltas.items()
            if dimension in _STYLE_DIMENSIONS and value != 0
        }
        effective = apply_style_deltas(base_profile, deltas)
        return GroupStyleState(
            scope_id=str(scope_id),
            deltas=deltas,
            effective_profile=effective,
            evidence=evidence_summary,
            evaluated_at=now,
            window_days=self.window_days,
        )
