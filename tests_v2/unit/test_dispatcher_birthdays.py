from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, PrimaryAction, ReasonCode, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.services.dispatcher import DispatcherPolicyState, decide


def birthday_event():
    return EventEnvelope(
        event_id="birthday:g1:u1:2026",
        event_type=EventType.BIRTHDAY_DUE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="g1",
        actor_user_id="u1",
        text=None,
    )


def test_birthday_due_bypasses_cooldown_and_daily_limits():
    decision=decide(
        birthday_event(),
        SceneAnalysis(),
        DispatcherPolicyState(
            cooldown_active=True,
            unsolicited_today=99,
            soft_daily_limit=1,
            hard_daily_limit=2,
        ),
    )

    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_BANTER
    assert decision.reason_codes == [ReasonCode.BIRTHDAY]
    assert decision.target_user_id == "u1"
    assert decision.metadata["unsolicited"] is False


def test_group_mute_still_blocks_birthday_due():
    decision=decide(
        birthday_event(),
        SceneAnalysis(),
        DispatcherPolicyState(group_muted=True),
    )

    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.SILENCE_REQUESTED]
