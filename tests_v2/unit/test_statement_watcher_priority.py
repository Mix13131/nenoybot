from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, PrimaryAction, ReasonCode, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.services.dispatcher import DispatcherPolicyState, decide
from app_v2.services.statement_watcher import StatementWatcher


def event(text: str = "передумал, никуда не иду") -> EventEnvelope:
    return EventEnvelope(
        event_id="e-priority",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="22",
        text=text,
    )


def test_priority_statement_respects_cooldown_in_initiative_v2() -> None:
    state = DispatcherPolicyState(
        cooldown_active=True,
        bot_spoke_recently=True,
        priority_statement=True,
        allow_callbacks=True,
    )
    scene = SceneAnalysis(callback_opportunity=.95, contradiction_score=.94, roast_opportunity=.9)

    decision = decide(event(), scene, state)

    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.COOLDOWN_ACTIVE]


def test_priority_statement_still_respects_hard_daily_limit_and_mute() -> None:
    scene = SceneAnalysis(callback_opportunity=.95, contradiction_score=.94)

    hard_limited = decide(
        event(),
        scene,
        DispatcherPolicyState(
            priority_statement=True,
            allow_callbacks=True,
            unsolicited_today=10,
            hard_daily_limit=10,
        ),
    )
    muted = decide(
        event(),
        scene,
        DispatcherPolicyState(
            priority_statement=True,
            allow_callbacks=True,
            group_muted=True,
        ),
    )

    assert hard_limited.primary_action is PrimaryAction.IGNORE
    assert muted.primary_action is PrimaryAction.IGNORE


class NoCallAdapter:
    def generate_json(self, *args, **kwargs):
        raise AssertionError("watcher must reject cross-author memory before model call")


def test_watcher_rejects_memory_from_another_author() -> None:
    now = datetime.now(timezone.utc)
    card = MemoryCard(
        id="foreign",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        memory_type="commitment",
        subject_keys=["user:999"],
        summary="Другой человек обещал прийти.",
        payload={"statement_kind": "commitment", "status": "open"},
        importance=.9,
        confidence=.85,
        freshness=1.0,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.INFERRED,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=True, proactive=True),
        evidence=[MemoryEvidence(message_id="10", author_id="999", timestamp=now, excerpt="Я буду")],
        source_count=1,
        created_at=now,
        updated_at=now,
    )

    result = StatementWatcher(NoCallAdapter()).evaluate(
        event=event(),
        scene=SceneAnalysis(),
        candidates=[card],
    )

    assert result.relation == "none"
    assert result.memory_id is None
