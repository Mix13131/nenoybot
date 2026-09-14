from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.memory import MemoryCard, UsagePolicy
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.repositories.memory_repo import RankedMemory
from app_v2.services.dispatcher import decide
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.personality_engine import PersonalityEngine


def event(text="уже еду", *, event_type=EventType.GROUP_MESSAGE) -> EventEnvelope:
    return EventEnvelope(
        event_id="e1",
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="9",
        text=text,
    )


def context(*, profile=None, participant=None) -> GroupContext:
    return GroupContext(
        internal_chat_id=7,
        telegram_chat_id="-100777",
        title="Friends",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile=profile or {
            "profile": "friends",
            "unsolicited_enabled": True,
            "initiative": 8,
            "roast": 9,
            "callback": 10,
            "profanity_level": 8,
            "profanity_frequency": 5,
            "callback_fatigue_minutes": 180,
        },
        participant=ParticipantContext(
            telegram_user_id="123",
            display_name="Anton",
            profile=participant or {"roast_tolerance": 7},
        ),
    )


def memory(memory_id: str, memory_type: str, *, payload=None, confidence=.95) -> RankedMemory:
    now=datetime.now(timezone.utc)
    card=MemoryCard(
        id=memory_id,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        memory_type=memory_type,
        subject_keys=["user:123"],
        summary=f"memory {memory_id}",
        payload=payload or {},
        importance=.9,
        confidence=confidence,
        freshness=.95,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.INFERRED,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=True, proactive=True),
        created_at=now,
        updated_at=now,
    )
    return RankedMemory(card=card, score=.9)


class FakeRetrieval:
    def __init__(self, items):
        self.items=list(items)
        self.calls=[]
    def retrieve(self, scope_type, scope_id, **kwargs):
        self.calls.append((scope_type, scope_id, kwargs))
        return [item for item in self.items if item.card.scope_type is scope_type and item.card.scope_id == scope_id]


class FailingRetrieval:
    def retrieve(self, *args, **kwargs):
        raise RuntimeError("memory store unavailable")


class FailingInitiative:
    def evaluate(self, **kwargs):
        raise RuntimeError("initiative state unavailable")


def test_running_joke_strengthens_grounded_callback_and_selects_callback_mode() -> None:
    retrieval=FakeRetrieval([memory("j1","running_joke")])
    engine=GroupBehaviorEngine(retrieval)
    scene=SceneAnalysis(roast_opportunity=.82, banter_score=.9)
    plan=engine.plan(event=event(), group_context=context(), scene=scene, now=datetime.now(timezone.utc))
    decision=decide(event(), plan.scene, plan.state)

    assert plan.callback_memory_ids == ("j1",)
    assert plan.memory_usage == "callback"
    assert plan.state.running_joke_fit is True
    assert plan.scene.callback_opportunity >= .82
    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_CALLBACK
    assert retrieval.calls[0][0] is ScopeType.GROUP
    assert retrieval.calls[0][1] == "-100777"
    assert retrieval.calls[0][2]["callback_fatigue_minutes"] == 180


def test_exhausted_or_missing_running_joke_cannot_force_callback() -> None:
    retrieval=FakeRetrieval([])
    plan=GroupBehaviorEngine(retrieval).plan(
        event=event(), group_context=context(), scene=SceneAnalysis(), now=datetime.now(timezone.utc)
    )
    decision=decide(event(), plan.scene, plan.state)
    assert plan.callback_memory_ids == ()
    assert plan.state.allow_callbacks is False
    assert decision.primary_action is PrimaryAction.IGNORE


def test_broken_commitment_sets_dispatcher_reason() -> None:
    retrieval=FakeRetrieval([memory("c1","commitment",payload={"status":"broken"})])
    scene=SceneAnalysis(roast_opportunity=.82)
    plan=GroupBehaviorEngine(retrieval).plan(
        event=event("я же обещал"), group_context=context(), scene=scene, now=datetime.now(timezone.utc)
    )
    decision=decide(event("я же обещал"), plan.scene, plan.state)
    assert plan.state.broken_commitment_relevant is True
    assert any(reason.value == "broken_commitment" for reason in decision.reason_codes)


def test_contradiction_with_grounded_memory_can_intervene() -> None:
    retrieval=FakeRetrieval([memory("x1","contradiction")])
    scene=SceneAnalysis(contradiction_score=.9, roast_opportunity=.85)
    plan=GroupBehaviorEngine(retrieval).plan(
        event=event("я не переобувался"), group_context=context(), scene=scene, now=datetime.now(timezone.utc)
    )
    decision=decide(event("я не переобувался"), plan.scene, plan.state)
    assert decision.primary_action is PrimaryAction.REPLY
    assert any(reason.value == "contradiction" for reason in decision.reason_codes)


def test_profanity_zero_is_hard_group_ceiling() -> None:
    profile=dict(context().profile)
    profile["profanity_level"]=0
    profile["profanity_frequency"]=0
    plan=GroupBehaviorEngine(FakeRetrieval([])).plan(
        event=event(), group_context=context(profile=profile), scene=SceneAnalysis(), now=datetime.now(timezone.utc)
    )
    personality=PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_BANTER,
        scene=plan.scene,
        context_profile=plan.context_profile,
    )
    assert personality.profanity_level == 0
    assert personality.profanity_frequency == 0


def test_high_profanity_is_suppressed_in_serious_scene() -> None:
    scene=SceneAnalysis(seriousness_score=.95, conflict_score=.9, roast_opportunity=1.0)
    plan=GroupBehaviorEngine(FakeRetrieval([memory("j1","running_joke")])).plan(
        event=event(), group_context=context(), scene=scene, now=datetime.now(timezone.utc)
    )
    personality=PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_ROAST,
        scene=plan.scene,
        context_profile=plan.context_profile,
    )
    assert plan.state.allow_roast is False
    assert personality.roast <= 2
    assert personality.profanity_level <= 4


def test_low_roast_tolerance_blocks_roast_and_callback_probe() -> None:
    retrieval=FakeRetrieval([memory("j1","running_joke")])
    plan=GroupBehaviorEngine(retrieval).plan(
        event=event(),
        group_context=context(participant={"roast_tolerance":2,"personality_modifiers":{"roast":-5}}),
        scene=SceneAnalysis(roast_opportunity=1.0),
        now=datetime.now(timezone.utc),
    )
    decision=decide(event(), plan.scene, plan.state)
    assert plan.state.allow_roast is False
    assert plan.state.allow_callbacks is False
    assert retrieval.calls == []
    assert decision.primary_action is PrimaryAction.IGNORE


def test_unsolicited_feature_flag_off_preserves_silence() -> None:
    profile=dict(context().profile)
    profile["unsolicited_enabled"]=False
    plan=GroupBehaviorEngine(FakeRetrieval([memory("j1","running_joke")])).plan(
        event=event(), group_context=context(profile=profile), scene=SceneAnalysis(roast_opportunity=.9), now=datetime.now(timezone.utc)
    )
    decision=decide(event(), plan.scene, plan.state)
    assert plan.state.cooldown_active is True
    assert decision.primary_action is PrimaryAction.IGNORE


def test_memory_failure_forces_unsolicited_silence_and_no_callback_claims() -> None:
    scene=SceneAnalysis(callback_opportunity=.95, roast_opportunity=.95, contradiction_score=.9)
    plan=GroupBehaviorEngine(FailingRetrieval()).plan(
        event=event(), group_context=context(), scene=scene, now=datetime.now(timezone.utc)
    )
    decision=decide(event(), plan.scene, plan.state)

    assert plan.callback_memory_ids == ()
    assert plan.memory_usage == "assist"
    assert plan.state.allow_callbacks is False
    assert plan.state.allow_roast is False
    assert plan.state.cooldown_active is True
    assert plan.state.metadata["policy_degraded"] is True
    assert plan.state.metadata["memory_unavailable"] is True
    assert decision.primary_action is PrimaryAction.IGNORE


def test_direct_mention_still_replies_when_group_memory_is_unavailable() -> None:
    direct=event("@nenoy что думаешь?", event_type=EventType.DIRECT_MENTION)
    plan=GroupBehaviorEngine(FailingRetrieval()).plan(
        event=direct,
        group_context=context(),
        scene=SceneAnalysis(direct_mention=True, callback_opportunity=1.0, roast_opportunity=1.0),
        now=datetime.now(timezone.utc),
    )
    decision=decide(direct, plan.scene, plan.state)

    assert plan.callback_memory_ids == ()
    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.mode is ResponseMode.GROUP_DIRECT_REPLY
    assert decision.mode is not ResponseMode.GROUP_CALLBACK


def test_initiative_failure_forces_unsolicited_silence_but_keeps_explicit_path() -> None:
    engine=GroupBehaviorEngine(FakeRetrieval([]), initiative_service=FailingInitiative())
    ordinary=event("ну и денек")
    plan=engine.plan(
        event=ordinary, group_context=context(), scene=SceneAnalysis(banter_score=.9), now=datetime.now(timezone.utc)
    )
    ordinary_decision=decide(ordinary, plan.scene, plan.state)

    assert plan.state.cooldown_active is True
    assert plan.state.metadata["initiative_unavailable"] is True
    assert ordinary_decision.primary_action is PrimaryAction.IGNORE

    direct=event("@nenoy?", event_type=EventType.DIRECT_MENTION)
    direct_plan=engine.plan(
        event=direct,
        group_context=context(),
        scene=SceneAnalysis(direct_mention=True),
        now=datetime.now(timezone.utc),
    )
    direct_decision=decide(direct, direct_plan.scene, direct_plan.state)
    assert direct_decision.primary_action is PrimaryAction.REPLY
    assert direct_decision.mode is ResponseMode.GROUP_DIRECT_REPLY
