from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.services.initiative_opportunity import evaluate_initiative_opportunity


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def event(event_type: EventType = EventType.GROUP_MESSAGE) -> EventEnvelope:
    return EventEnvelope(
        event_id="opp-1",
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123" if event_type is EventType.GROUP_MESSAGE else None,
        message_id="55" if event_type is EventType.GROUP_MESSAGE else None,
        text="обычная реплика" if event_type is EventType.GROUP_MESSAGE else None,
        metadata={},
    )


def opportunity(
    scene: SceneAnalysis | None = None,
    *,
    event_type: EventType = EventType.GROUP_MESSAGE,
    has_grounded_callback: bool = False,
    grounded_contradiction: bool = False,
    broken_commitment: bool = False,
    priority_statement: bool = False,
    allow_callbacks: bool = True,
    allow_roast: bool = True,
    ignored_recent: int = 0,
    negative_feedback_recent: int = 0,
    positive_feedback_recent: int = 0,
    bot_spoke_recently: bool = False,
    policy_degraded: bool = False,
):
    return evaluate_initiative_opportunity(
        event(event_type),
        scene or SceneAnalysis(),
        has_grounded_callback=has_grounded_callback,
        grounded_contradiction=grounded_contradiction,
        broken_commitment=broken_commitment,
        priority_statement=priority_statement,
        allow_callbacks=allow_callbacks,
        allow_roast=allow_roast,
        ignored_recent=ignored_recent,
        negative_feedback_recent=negative_feedback_recent,
        positive_feedback_recent=positive_feedback_recent,
        bot_spoke_recently=bot_spoke_recently,
        policy_degraded=policy_degraded,
    )


def test_weak_group_chatter_is_no_action() -> None:
    result = opportunity()
    assert result.eligible is False
    assert result.suppressors == ("no_grounded_hook",)
    assert result.quality_score == 0


def test_positive_feedback_and_high_activity_never_create_hook_from_nothing() -> None:
    result = opportunity(positive_feedback_recent=100)
    assert result.eligible is False
    assert result.hook_family is None


def test_fresh_clear_help_can_be_opportunity_eligible() -> None:
    result = opportunity(SceneAnalysis(help_opportunity=0.95))
    assert result.eligible is True
    assert result.hook_family == "help"
    assert result.quality_score == 80


def test_fresh_very_strong_banter_can_be_opportunity_eligible() -> None:
    result = opportunity(
        SceneAnalysis(banter_score=0.95, roast_opportunity=0.95)
    )
    assert result.eligible is True
    assert result.hook_family == "banter"


def test_silence_duration_does_not_make_stale_help_or_banter_eligible() -> None:
    result = opportunity(
        SceneAnalysis(
            help_opportunity=1.0,
            banter_score=1.0,
            roast_opportunity=1.0,
        ),
        event_type=EventType.GROUP_SILENCE_WAKEUP,
    )
    assert result.eligible is False
    assert result.stale_context is True
    assert result.suppressors == ("no_grounded_hook",)


def test_stale_context_requires_strong_grounded_callback() -> None:
    result = opportunity(
        SceneAnalysis(callback_opportunity=0.90),
        event_type=EventType.GROUP_SILENCE_WAKEUP,
        has_grounded_callback=True,
    )
    assert result.eligible is True
    assert result.hook_family == "callback"
    assert result.quality_score == 85


def test_one_ignore_cools_borderline_banter_below_threshold() -> None:
    result = opportunity(
        SceneAnalysis(banter_score=0.95, roast_opportunity=0.95),
        ignored_recent=1,
    )
    assert result.eligible is False
    assert result.quality_score == 57
    assert result.suppressors == ("below_quality_threshold",)


def test_repeated_ignored_negative_recent_bot_and_unsafe_scene_fail_closed() -> None:
    cases = (
        {"ignored_recent": 2},
        {"negative_feedback_recent": 1},
        {"bot_spoke_recently": True},
        {"policy_degraded": True},
    )
    for overrides in cases:
        result = opportunity(
            SceneAnalysis(help_opportunity=1.0),
            **overrides,
        )
        assert result.eligible is False
        assert result.suppressors

    unsafe = opportunity(
        SceneAnalysis(help_opportunity=1.0, conflict_score=0.8)
    )
    assert unsafe.eligible is False
    assert "unsafe_scene" in unsafe.suppressors


def test_positive_feedback_only_modestly_reinforces_existing_hook() -> None:
    result = opportunity(
        SceneAnalysis(banter_score=0.95, roast_opportunity=0.95),
        positive_feedback_recent=3,
    )
    assert result.eligible is True
    assert result.quality_score == 77
    assert "positive_feedback_support" in result.reasons
