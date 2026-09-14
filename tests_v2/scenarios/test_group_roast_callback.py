from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.memory import MemoryCard, UsagePolicy
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.repositories.memory_repo import RankedMemory
from app_v2.services.context_builder import GenerationContext, GenerationMemory
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.personality_engine import PersonalityEngine


def _event(event_id="ride-1"):
    return EventEnvelope(
        event_id=event_id,
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="77",
        text="уже еду",
    )


def _card():
    now=datetime.now(timezone.utc)
    return MemoryCard(
        id="joke-already-going",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        memory_type="running_joke",
        subject_keys=["user:123"],
        summary="Фраза «уже еду» регулярно появляется до фактического выезда.",
        importance=.9,
        confidence=.96,
        freshness=.95,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.INFERRED,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=True, proactive=True),
        created_at=now,
        updated_at=now,
    )


class Access:
    def __init__(self, tolerance=7):
        self.ctx=GroupContext(
            internal_chat_id=1,
            telegram_chat_id="-100777",
            title="Friends",
            is_whitelisted=True,
            is_active=True,
            silent_until=None,
            profile={
                "profile":"friends","unsolicited_enabled":True,"initiative":8,
                "roast":9,"callback":10,"profanity_level":8,"profanity_frequency":5,
                "callback_fatigue_minutes":180,
            },
            participant=ParticipantContext(
                telegram_user_id="123", display_name="Anton",
                profile={"roast_tolerance":tolerance},
            ),
        )
    def evaluate(self, event, *, now=None): return GroupAccessResult(True,"allowed",self.ctx)


class Retrieval:
    def __init__(self, exhausted=False): self.exhausted=exhausted; self.calls=[]
    def retrieve(self, scope_type, scope_id, **kwargs):
        self.calls.append((scope_type,scope_id,kwargs))
        if self.exhausted: return []
        return [RankedMemory(_card(),.95)]


class Analyzer:
    def analyze(self,event): return SceneAnalysis(banter_score=.9,roast_opportunity=.86)


class Context:
    def __init__(self): self.calls=[]
    def build(self, **kwargs):
        self.calls.append(kwargs)
        event=kwargs["event"]; decision=kwargs["decision"]; p=kwargs["personality"]
        memories=()
        if kwargs["memory_usage"]=="callback":
            memories=(GenerationMemory("joke-already-going","running_joke","уже еду раньше выезда",.96,.9,()),)
        return GenerationContext(
            scope_type=ScopeType.GROUP,scope_id=event.scope_id,
            event=event.model_dump(mode="json"),scene=kwargs["scene"].model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),personality=p.model_dump(mode="json"),
            hot_messages=(),memories=memories,target_user_id=decision.target_user_id,
            action_state=kwargs.get("action_state",{}),estimated_hot_tokens=0,estimated_memory_tokens=20,
        )


class Generator:
    def __init__(self): self.calls=[]
    def generate(self,ctx): self.calls.append(ctx); return SimpleNamespace(text="58 минут. Крепкий мужик.")

class Interventions:
    def __init__(self): self.rows=[]
    def record(self,**kwargs): self.rows.append(kwargs); return SimpleNamespace(id=len(self.rows))

class Outbox:
    def __init__(self): self.rows={}
    def enqueue(self,msg):
        if msg.dedupe_key in self.rows: return self.rows[msg.dedupe_key],False
        oid=len(self.rows)+1; self.rows[msg.dedupe_key]=oid; return oid,True


def _pipeline(retrieval, *, tolerance=7):
    return GroupPipeline(
        access_service=Access(tolerance),scene_analyzer=Analyzer(),personality_engine=PersonalityEngine(),
        context_builder=Context(),response_generator=Generator(),intervention_repo=Interventions(),outbox_repo=Outbox(),
        group_behavior_engine=GroupBehaviorEngine(retrieval),
    )


def test_already_going_running_joke_flows_to_grounded_group_callback() -> None:
    retrieval=Retrieval()
    p=_pipeline(retrieval)
    result=p.process(_event())
    assert result.primary_action is PrimaryAction.REPLY
    assert result.mode is ResponseMode.GROUP_CALLBACK
    assert result.outbox_created is True
    assert p.context_builder.calls[0]["memory_usage"] == "callback"
    assert p.context_builder.calls[0]["callback_fatigue_minutes"] == 180
    assert p.response_generator.calls[0].memories[0].id == "joke-already-going"
    assert retrieval.calls[0][0] is ScopeType.GROUP


def test_exhausted_running_joke_returns_to_silence() -> None:
    p=_pipeline(Retrieval(exhausted=True))
    result=p.process(_event("ride-2"))
    assert result.primary_action is PrimaryAction.IGNORE
    assert result.outbox_id is None


def test_negative_target_tolerance_suppresses_unsolicited_roast() -> None:
    p=_pipeline(Retrieval(),tolerance=2)
    result=p.process(_event("ride-3"))
    assert result.primary_action is PrimaryAction.IGNORE
    assert result.outbox_id is None
