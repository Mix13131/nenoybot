from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, PrimaryAction, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.repositories.memory_repo import RankedMemory
from app_v2.services.dispatcher import decide
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.statement_watcher import StatementWatchResult


class Retrieval:
    def __init__(self, card):
        self.card = card
    def retrieve(self, *args, **kwargs):
        return [RankedMemory(card=self.card, score=.9)]


class Watcher:
    def __init__(self, result):
        self.result = result
        self.calls = 0
    def evaluate(self, **kwargs):
        self.calls += 1
        return self.result


def group_context() -> GroupContext:
    return GroupContext(
        internal_chat_id=1,
        telegram_chat_id="-100777",
        title="Лучшие ЗДЕСЬ",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile={
            "profile": "friends",
            "unsolicited_enabled": True,
            "initiative": 3,
            "roast": 8,
            "callback": 8,
            "callback_fatigue_minutes": 180,
        },
        participant=ParticipantContext(
            telegram_user_id="123",
            display_name="Сергей",
            profile={"roast_tolerance": 7},
        ),
    )


def commitment() -> MemoryCard:
    now = datetime.now(timezone.utc)
    return MemoryCard(
        id="c1",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        memory_type="commitment",
        subject_keys=["user:123"],
        summary="Сергей сказал, что завтра идёт в баню.",
        payload={"statement_kind": "commitment", "status": "open"},
        importance=.9,
        confidence=.84,
        freshness=1.0,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.INFERRED,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=True, proactive=True),
        evidence=[MemoryEvidence(message_id="10", author_id="123", timestamp=now, excerpt="Завтра баня, я буду")],
        source_count=1,
        created_at=now,
        updated_at=now,
    )


def current_event(text="не, я завтра не иду") -> EventEnvelope:
    return EventEnvelope(
        event_id="e2",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="11",
        text=text,
    )


def test_strong_broken_commitment_can_trigger_autonomous_reply() -> None:
    watch = Watcher(StatementWatchResult(
        relation="broken_commitment",
        confidence=.95,
        roast_fit=.9,
        memory_id="c1",
        memory_summary="Сергей сказал, что завтра идёт в баню.",
        evidence_excerpt="Завтра баня, я буду",
    ))
    engine = GroupBehaviorEngine(Retrieval(commitment()), statement_watcher=watch)
    plan = engine.plan(
        event=current_event(),
        group_context=group_context(),
        scene=SceneAnalysis(banter_score=.7),
        now=datetime.now(timezone.utc),
    )
    decision = decide(current_event(), plan.scene, plan.state)

    assert watch.calls == 1
    assert plan.statement_watch["relation"] == "broken_commitment"
    assert plan.callback_memory_ids[0] == "c1"
    assert plan.scene.contradiction_score >= .90
    assert plan.scene.callback_opportunity >= .94
    assert plan.state.broken_commitment_relevant is True
    assert decision.primary_action is PrimaryAction.REPLY


def test_weak_statement_callback_does_not_turn_every_message_into_reply() -> None:
    watch = Watcher(StatementWatchResult(
        relation="callback",
        confidence=.75,
        roast_fit=.6,
        memory_id="c1",
        memory_summary="Сергей сказал, что завтра идёт в баню.",
        evidence_excerpt="Завтра баня, я буду",
    ))
    engine = GroupBehaviorEngine(Retrieval(commitment()), statement_watcher=watch)
    plan = engine.plan(
        event=current_event("привет всем"),
        group_context=group_context(),
        scene=SceneAnalysis(),
        now=datetime.now(timezone.utc),
    )
    decision = decide(current_event("привет всем"), plan.scene, plan.state)

    assert watch.calls == 1
    assert plan.scene.callback_opportunity < .84
    assert decision.primary_action is PrimaryAction.IGNORE
