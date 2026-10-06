from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.domain.enums import EventType
from app_v2.domain.events import EventEnvelope, SceneAnalysis


@dataclass(frozen=True)
class InitiativeOpportunity:
    eligible: bool
    quality_score: int
    hook_family: str | None
    reasons: tuple[str, ...]
    suppressors: tuple[str, ...]
    stale_context: bool

    def as_metadata(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "quality_score": self.quality_score,
            "hook_family": self.hook_family,
            "reasons": list(self.reasons),
            "suppressors": list(self.suppressors),
            "stale_context": self.stale_context,
            "version": "initiative-opportunity-v1",
        }


def evaluate_initiative_opportunity(
    event: EventEnvelope,
    scene: SceneAnalysis,
    *,
    has_grounded_callback: bool,
    grounded_contradiction: bool,
    broken_commitment: bool,
    priority_statement: bool,
    allow_callbacks: bool,
    allow_roast: bool,
    ignored_recent: int,
    negative_feedback_recent: int,
    positive_feedback_recent: int,
    bot_spoke_recently: bool,
    policy_degraded: bool,
) -> InitiativeOpportunity:
    """Judge whether an unsolicited turn has enough material to justify speech.

    This is deliberately a gate, not a generator. It never creates a semantic
    opportunity from silence, elapsed time, initiative level or positive
    feedback alone.
    """

    stale = event.event_type is EventType.GROUP_SILENCE_WAKEUP
    suppressors: list[str] = []

    if policy_degraded:
        suppressors.append("policy_degraded")
    if (
        scene.seriousness_score >= 0.75
        or scene.conflict_score >= 0.75
        or scene.sensitivity_score >= 0.75
    ):
        suppressors.append("unsafe_scene")
    if negative_feedback_recent > 0:
        suppressors.append("negative_feedback_recent")
    if ignored_recent >= 2:
        suppressors.append("repeated_ignored")
    if bot_spoke_recently:
        suppressors.append("recent_unsolicited_bot")

    if suppressors:
        return InitiativeOpportunity(
            eligible=False,
            quality_score=0,
            hook_family=None,
            reasons=(),
            suppressors=tuple(suppressors),
            stale_context=stale,
        )

    candidates: list[tuple[int, str, tuple[str, ...]]] = []

    if priority_statement and allow_callbacks:
        candidates.append(
            (100, "statement_watch", ("priority_statement", "grounded_callback"))
        )

    if allow_callbacks and has_grounded_callback:
        if grounded_contradiction and scene.contradiction_score >= 0.85:
            candidates.append(
                (
                    95,
                    "callback",
                    ("grounded_callback", "grounded_contradiction"),
                )
            )
        if broken_commitment:
            candidates.append(
                (
                    92,
                    "callback",
                    ("grounded_callback", "broken_commitment"),
                )
            )
        if scene.callback_opportunity >= 0.82:
            score = 85
            reasons = ["grounded_callback"]
            if grounded_contradiction and scene.contradiction_score >= 0.75:
                score = 95
                reasons.append("grounded_contradiction")
            if broken_commitment:
                score = max(score, 92)
                reasons.append("broken_commitment")
            candidates.append((score, "callback", tuple(reasons)))

    # Stale silence-wakeup context is intentionally much stricter. An old
    # excerpt is not permission to revive the room with generic help/banter.
    if not stale:
        if scene.help_opportunity >= 0.90:
            candidates.append((80, "help", ("fresh_help_opportunity",)))
        if (
            allow_roast
            and scene.banter_score >= 0.90
            and scene.roast_opportunity >= 0.90
        ):
            candidates.append((72, "banter", ("fresh_banter_opportunity",)))

    if not candidates:
        return InitiativeOpportunity(
            eligible=False,
            quality_score=0,
            hook_family=None,
            reasons=(),
            suppressors=("no_grounded_hook",),
            stale_context=stale,
        )

    score, family, reasons = max(candidates, key=lambda item: item[0])

    # One ignored proactive attempt cools borderline hooks. Repeated ignores
    # were already a hard suppressor above.
    if ignored_recent == 1:
        score = max(0, score - 15)
        reasons = (*reasons, "one_recent_ignore")

    # Positive feedback can modestly reinforce an existing hook, never create
    # one. Keep the bonus small and bounded.
    if positive_feedback_recent >= 3:
        score = min(100, score + 5)
        reasons = (*reasons, "positive_feedback_support")

    threshold = 85 if stale else 70
    eligible = score >= threshold

    return InitiativeOpportunity(
        eligible=eligible,
        quality_score=score,
        hook_family=family,
        reasons=tuple(reasons),
        suppressors=() if eligible else ("below_quality_threshold",),
        stale_context=stale,
    )
