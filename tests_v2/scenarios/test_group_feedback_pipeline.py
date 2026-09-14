from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, PrimaryAction, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.personality_engine import PersonalityEngine


class Access:
    def evaluate(self,event,*,now=None):
        return GroupAccessResult(True,"allowed",GroupContext(
            internal_chat_id=1,telegram_chat_id="-1001",title="Friends",
            is_whitelisted=True,is_active=True,silent_until=None,profile={},
            participant=ParticipantContext(telegram_user_id="123"),
        ))

class Collector:
    def __init__(self): self.events=[]
    def collect(self,event): self.events.append(event); return None

class Analyzer:
    def __init__(self): self.calls=[]
    def analyze(self,event): self.calls.append(event); raise AssertionError("reaction must not reach analyzer")

class Never:
    def __getattr__(self,name):
        def fail(*args,**kwargs): raise AssertionError(f"unexpected {name}")
        return fail


def test_group_reaction_is_collected_and_short_circuits_conversation_pipeline() -> None:
    collector=Collector(); analyzer=Analyzer()
    pipeline=GroupPipeline(
        access_service=Access(),scene_analyzer=analyzer,personality_engine=PersonalityEngine(),
        context_builder=Never(),response_generator=Never(),intervention_repo=Never(),outbox_repo=Never(),
        feedback_collector=collector,
    )
    event=EventEnvelope(
        event_id="tg:react",event_type=EventType.REACTION_ADDED,
        occurred_at=datetime.now(timezone.utc),scope_type=ScopeType.GROUP,
        scope_id="-1001",actor_user_id="123",message_id="88",
        metadata={"new_reaction":[{"type":"emoji","emoji":"🔥"}]},
    )
    result=pipeline.process(event)
    assert result.primary_action is PrimaryAction.IGNORE
    assert result.access_reason == "feedback_collected"
    assert collector.events == [event]
    assert analyzer.calls == []
