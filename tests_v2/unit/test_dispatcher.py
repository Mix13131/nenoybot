from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, PrimaryAction, ReasonCode, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.services.dispatcher import POLICY_VERSION, DispatcherPolicyState, decide


def _event(
    *,
    scope: ScopeType = ScopeType.GROUP,
    event_type: EventType | None = None,
) -> EventEnvelope:
    if event_type is None:
        event_type = EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE
    return EventEnvelope(
        event_id="evt-1",
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope,
        scope_id="u1" if scope is ScopeType.PERSONAL else "g1",
        actor_user_id="u1",
        message_id="m1",
        text="test",
    )


def test_personal_message_defaults_to_reply() -> None:
    decision = decide(_event(scope=ScopeType.PERSONAL))

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.ASSISTANT
    assert decision.intervention_score == 100
    assert decision.reason_codes == [ReasonCode.PERSONAL_DEFAULT_REPLY]
    assert decision.metadata["policy_version"] == POLICY_VERSION


def test_personal_sensitive_context_uses_care_candidate() -> None:
    decision = decide(
        _event(scope=ScopeType.PERSONAL),
        SceneAnalysis(sensitivity_score=0.9),
    )
    assert decision.mode is ResponseMode.CARE


def test_direct_mention_replies_despite_cooldown_and_limit() -> None:
    decision = decide(
        _event(event_type=EventType.DIRECT_MENTION),
        SceneAnalysis(direct_mention=True),
        DispatcherPolicyState(cooldown_active=True, unsolicited_today=99, hard_daily_limit=10),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_DIRECT_REPLY
    assert ReasonCode.DIRECT_MENTION in decision.reason_codes
    assert decision.metadata["unsolicited"] is False


def test_reply_to_bot_replies_despite_cooldown() -> None:
    decision = decide(
        _event(event_type=EventType.REPLY_TO_BOT),
        SceneAnalysis(reply_to_bot=True),
        DispatcherPolicyState(cooldown_active=True),
    )
    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_DIRECT_REPLY
    assert ReasonCode.REPLY_TO_BOT in decision.reason_codes


def test_ordinary_group_message_is_silent() -> None:
    decision = decide(_event(), SceneAnalysis(), DispatcherPolicyState())

    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.intervention_score == 0
    assert decision.reason_codes == [ReasonCode.GROUP_DEFAULT_SILENCE]


def test_group_muted_blocks_unsolicited() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(callback_opportunity=1, contradiction_score=1, roast_opportunity=1),
        DispatcherPolicyState(group_muted=True),
    )
    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.SILENCE_REQUESTED]


def test_cooldown_blocks_unsolicited() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(callback_opportunity=1, contradiction_score=1, roast_opportunity=1),
        DispatcherPolicyState(cooldown_active=True),
    )
    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.COOLDOWN_ACTIVE]


def test_hard_daily_limit_blocks_unsolicited() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(callback_opportunity=1, contradiction_score=1, roast_opportunity=1),
        DispatcherPolicyState(unsolicited_today=10, hard_daily_limit=10),
    )
    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.HARD_DAILY_LIMIT]


def test_strong_callback_selects_callback_mode() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(
            callback_opportunity=0.95,
            contradiction_score=0.9,
            help_opportunity=0.9,
        ),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_CALLBACK
    assert decision.intervention_score == 85
    assert ReasonCode.CALLBACK_OPPORTUNITY in decision.reason_codes


def test_safe_strong_roast_selects_roast_mode() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(roast_opportunity=0.95, contradiction_score=0.9),
        DispatcherPolicyState(running_joke_fit=True),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_ROAST
    assert decision.intervention_score == 75


def test_serious_context_blocks_roast() -> None:
    decision = decide(
        _event(event_type=EventType.DIRECT_MENTION),
        SceneAnalysis(direct_mention=True, seriousness_score=0.95, roast_opportunity=1.0),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_DIRECT_REPLY
    assert decision.mode is not ResponseMode.GROUP_ROAST


def test_mid_score_requires_high_initiative() -> None:
    scene = SceneAnalysis(callback_opportunity=0.9, contradiction_score=0.9)  # 65

    low = decide(_event(), scene, DispatcherPolicyState(initiative_level=7))
    high = decide(_event(), scene, DispatcherPolicyState(initiative_level=8))

    assert low.primary_action is PrimaryAction.IGNORE
    assert low.intervention_score == 65
    assert high.primary_action is PrimaryAction.REPLY
    assert high.mode is ResponseMode.GROUP_CALLBACK
    assert ReasonCode.HIGH_INITIATIVE in high.reason_codes


def test_recent_bot_activity_and_ignored_intervention_reduce_score() -> None:
    decision = decide(
        _event(),
        SceneAnalysis(callback_opportunity=1, contradiction_score=1, roast_opportunity=1),
        DispatcherPolicyState(bot_spoke_recently=True, ignored_unsolicited_recent=2),
    )

    # 35 + 30 + 25 - 35 - 35 = 20
    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.intervention_score == 20
    assert ReasonCode.BOT_SPOKE_RECENTLY in decision.reason_codes
    assert ReasonCode.PREVIOUS_UNSOLICITED_IGNORED in decision.reason_codes



def test_silence_wakeup_gets_one_explicit_unsolicited_reply_path():
    decision = decide(
        _event(event_type=EventType.GROUP_SILENCE_WAKEUP),
        SceneAnalysis(),
        DispatcherPolicyState(
            cooldown_active=False,
            unsolicited_today=0,
            soft_daily_limit=6,
            hard_daily_limit=10,
        ),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_BANTER
    assert decision.reason_codes == [ReasonCode.SILENCE_REENGAGEMENT]
    assert decision.metadata["unsolicited"] is True
    assert decision.metadata["silence_reengagement"] is True


def test_silence_wakeup_can_use_grounded_callback_mode():
    decision = decide(
        _event(event_type=EventType.GROUP_SILENCE_WAKEUP),
        SceneAnalysis(callback_opportunity=0.9),
        DispatcherPolicyState(
            allow_callbacks=True,
            cooldown_active=False,
            unsolicited_today=0,
            soft_daily_limit=6,
        ),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_CALLBACK


def test_silence_wakeup_is_blocked_by_soft_limit():
    decision = decide(
        _event(event_type=EventType.GROUP_SILENCE_WAKEUP),
        SceneAnalysis(),
        DispatcherPolicyState(
            unsolicited_today=6,
            soft_daily_limit=6,
            hard_daily_limit=10,
        ),
    )

    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.SOFT_DAILY_LIMIT]


def test_silence_wakeup_is_blocked_by_serious_or_sensitive_scene():
    serious = decide(
        _event(event_type=EventType.GROUP_SILENCE_WAKEUP),
        SceneAnalysis(seriousness_score=0.8),
        DispatcherPolicyState(),
    )
    sensitive = decide(
        _event(event_type=EventType.GROUP_SILENCE_WAKEUP),
        SceneAnalysis(sensitivity_score=0.8),
        DispatcherPolicyState(),
    )

    assert serious.primary_action is PrimaryAction.IGNORE
    assert serious.reason_codes == [ReasonCode.SERIOUS_CONTEXT]
    assert sensitive.primary_action is PrimaryAction.IGNORE
    assert sensitive.reason_codes == [ReasonCode.SENSITIVE_CONTEXT]
