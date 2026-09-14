from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import MemoryOrigin, MemoryStatus, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, EventType, SceneAnalysis
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.repositories.memory_repo import RankedMemory
from app_v2.repositories.message_repo import HotMessage
from app_v2.services.context_builder import ContextBuilder
from app_v2.services.personality_engine import PersonalityEngine


class FakeMessageRepo:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def recent_for_scope(self, scope_type, scope_id, *, limit):
        self.calls.append((scope_type, scope_id, limit))
        return list(self.messages[-limit:])


class FakeRetrieval:
    def __init__(self, memories):
        self.memories = memories
        self.calls = []

    def retrieve(self, scope_type, scope_id, **kwargs):
        self.calls.append((scope_type, scope_id, kwargs))
        return [m for m in self.memories if m.card.scope_type is scope_type and m.card.scope_id == scope_id]


def _event(scope_type=ScopeType.PERSONAL, scope_id="u1"):
    return EventEnvelope(
        event_id="evt1",
        event_type=EventType.PRIVATE_MESSAGE if scope_type is ScopeType.PERSONAL else EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope_type,
        scope_id=scope_id,
        actor_user_id="u1",
        message_id="100",
        text="Что дальше?",
    )


def _decision(scope_type=ScopeType.PERSONAL):
    mode = ResponseMode.ASSISTANT if scope_type is ScopeType.PERSONAL else ResponseMode.GROUP_HELP
    return DispatcherDecision(primary_action=PrimaryAction.REPLY, mode=mode)


def _memory(memory_id, scope_type, scope_id, summary="Полезная память"):
    now = datetime.now(timezone.utc)
    card = MemoryCard(
        id=memory_id,
        scope_type=scope_type,
        scope_id=scope_id,
        memory_type="observation",
        subject_keys=["user:u1"],
        summary=summary,
        importance=0.8,
        confidence=0.9,
        freshness=0.9,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.EXPLICIT,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=False, proactive=False),
        evidence=[MemoryEvidence(message_id="90", author_id="u1", timestamp=now, excerpt="evidence excerpt")],
        created_at=now,
        updated_at=now,
    )
    return RankedMemory(card=card, score=0.8)


def _messages(count, size=80):
    now = datetime.now(timezone.utc)
    return [
        HotMessage(str(i), "u1", f"{i}:" + ("x" * size), now)
        for i in range(count)
    ]


def test_large_chat_is_bounded_by_hot_token_budget():
    messages = _messages(300, size=300)
    builder = ContextBuilder(
        message_repo=FakeMessageRepo(messages),
        retrieval_engine=FakeRetrieval([]),
        hot_max_messages=100,
        hot_token_budget=500,
    )
    event = _event()
    context = builder.build(
        event=event,
        scene=SceneAnalysis(),
        decision=_decision(),
        personality=PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.ASSISTANT),
    )
    assert len(context.hot_messages) < 100
    assert context.estimated_hot_tokens <= 500


def test_memory_count_and_budget_are_bounded():
    memories = [_memory(f"m{i}", ScopeType.PERSONAL, "u1", summary="s" * 300) for i in range(20)]
    builder = ContextBuilder(
        message_repo=FakeMessageRepo([]),
        retrieval_engine=FakeRetrieval(memories),
        memory_max_cards=8,
        memory_token_budget=500,
    )
    context = builder.build(
        event=_event(),
        scene=SceneAnalysis(),
        decision=_decision(),
        personality=PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.ASSISTANT),
    )
    assert len(context.memories) <= 8
    assert context.estimated_memory_tokens <= 500


def test_group_context_never_requests_personal_scope():
    memories = [
        _memory("personal", ScopeType.PERSONAL, "u1"),
        _memory("group", ScopeType.GROUP, "g1"),
    ]
    retrieval = FakeRetrieval(memories)
    messages = FakeMessageRepo([])
    builder = ContextBuilder(message_repo=messages, retrieval_engine=retrieval)
    event = _event(ScopeType.GROUP, "g1")
    context = builder.build(
        event=event,
        scene=SceneAnalysis(),
        decision=_decision(ScopeType.GROUP),
        personality=PersonalityEngine().build(scope_type=ScopeType.GROUP, mode=ResponseMode.GROUP_HELP),
        subject_keys=["user:u1"],
    )
    assert [m.id for m in context.memories] == ["group"]
    assert retrieval.calls[0][0] is ScopeType.GROUP
    assert retrieval.calls[0][1] == "g1"
    assert messages.calls[0][0] is ScopeType.GROUP
    assert messages.calls[0][1] == "g1"


def test_memory_evidence_is_preserved_for_callback_grounding():
    builder = ContextBuilder(
        message_repo=FakeMessageRepo([]),
        retrieval_engine=FakeRetrieval([_memory("m1", ScopeType.PERSONAL, "u1")]),
    )
    context = builder.build(
        event=_event(),
        scene=SceneAnalysis(),
        decision=_decision(),
        personality=PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.ASSISTANT),
    )
    assert context.memories[0].evidence[0]["excerpt"] == "evidence excerpt"
    assert context.memories[0].evidence[0]["message_id"] == "90"


def test_context_contains_decision_personality_and_target():
    decision = _decision().model_copy(update={"target_user_id": "u2"})
    personality = PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.ASSISTANT)
    context = ContextBuilder(
        message_repo=FakeMessageRepo([]),
        retrieval_engine=FakeRetrieval([]),
    ).build(
        event=_event(),
        scene=SceneAnalysis(help_opportunity=0.8),
        decision=decision,
        personality=personality,
        action_state={"task": "demo"},
    )
    assert context.target_user_id == "u2"
    assert context.decision["primary_action"] == "reply"
    assert context.personality["directness"] == 8
    assert context.action_state == {"task": "demo"}
