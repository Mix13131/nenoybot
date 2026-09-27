from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.context_builder import GenerationContext
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.personality_engine import PersonalityEngine


class Access:
    def evaluate(self, event, *, now=None):
        return GroupAccessResult(
            True,
            "allowed",
            GroupContext(
                internal_chat_id=1,
                telegram_chat_id="-1001",
                title="Friends",
                is_whitelisted=True,
                is_active=True,
                silent_until=None,
                profile={"profile": "friends"},
                participant=ParticipantContext(
                    telegram_user_id="42",
                    display_name="Anton",
                    profile={},
                ),
            ),
        )


class Analyzer:
    def __init__(self):
        self.calls=[]
    def analyze(self, event):
        self.calls.append(event)
        return SceneAnalysis()


class Context:
    def __init__(self):
        self.calls=[]
    def build(self, **kwargs):
        self.calls.append(kwargs)
        event=kwargs["event"]
        decision=kwargs["decision"]
        personality=kwargs["personality"]
        return GenerationContext(
            scope_type=ScopeType.GROUP,
            scope_id=event.scope_id,
            event=event.model_dump(mode="json"),
            scene=kwargs["scene"].model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),
            personality=personality.model_dump(mode="json"),
            hot_messages=(),
            memories=(),
            target_user_id=decision.target_user_id,
            action_state=kwargs.get("action_state", {}),
            estimated_hot_tokens=0,
            estimated_memory_tokens=0,
        )


class Generator:
    def __init__(self):
        self.calls=[]
    def generate(self, context):
        self.calls.append(context)
        return SimpleNamespace(text="С днём рождения, Антон!")


class Interventions:
    def __init__(self):
        self.rows=[]
    def record(self, **kwargs):
        self.rows.append(kwargs)
        return SimpleNamespace(id=len(self.rows))


class Outbox:
    def __init__(self):
        self.calls=[]
    def enqueue(self, message):
        self.calls.append(message)
        return 1, True


class Mapper:
    def __init__(self):
        self.calls=[]
    def map_event(self, event, **kwargs):
        self.calls.append((event, kwargs))
        return SimpleNamespace(written=(), forgotten_ids=(), failed=False)


class Birthday:
    def __init__(self, action):
        self.action=action
        self.calls=[]
        self.repo=SimpleNamespace(rollback=lambda: None)
    def observe_group_event(self, event, **kwargs):
        self.calls.append((event, kwargs))
        return self.action


def make_pipeline(*, birthday_service=None, mapper=None):
    analyzer=Analyzer()
    context=Context()
    generator=Generator()
    pipeline=GroupPipeline(
        access_service=Access(),
        scene_analyzer=analyzer,
        personality_engine=PersonalityEngine(),
        context_builder=context,
        response_generator=generator,
        intervention_repo=Interventions(),
        outbox_repo=Outbox(),
        memory_mapper=mapper,
        birthday_service=birthday_service,
        unsolicited_enabled=False,
    )
    return pipeline, analyzer, context, generator


def birthday_due_event():
    return EventEnvelope(
        event_id="birthday:-1001:42:2026",
        event_type=EventType.BIRTHDAY_DUE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id="42",
        message_id=None,
        text=None,
        metadata={
            "synthetic": True,
            "birthday_due": True,
            "birthday_user_id": "42",
            "birthday_name": "Anton",
            "birthday_day": 27,
            "birthday_month": 9,
            "age_allowed": False,
        },
    )


def test_birthday_due_bypasses_scene_analysis_and_generates_for_target():
    pipeline, analyzer, context, generator=make_pipeline()

    result=pipeline.process(birthday_due_event())

    assert result.primary_action is PrimaryAction.REPLY
    assert result.mode is ResponseMode.GROUP_BANTER
    assert result.outbox_created is True
    assert analyzer.calls == []
    call=context.calls[0]
    assert call["subject_keys"] == ["user:42"]
    assert call["action_state"]["birthday_due"]["birthday_name"] == "Anton"
    assert call["action_state"]["birthday_due"]["age_allowed"] is False
    assert len(generator.calls) == 1


def test_explicit_birthday_profile_operation_skips_generic_memory_mapper():
    mapper=Mapper()
    birthday=Birthday(
        {
            "status": "succeeded",
            "operation": "save",
            "day": 14,
            "month": 5,
            "year": None,
            "source": "explicit",
        }
    )
    pipeline, _, context, _=make_pipeline(
        birthday_service=birthday,
        mapper=mapper,
    )
    human_event=EventEnvelope(
        event_id="e1",
        event_type=EventType.DIRECT_MENTION,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id="42",
        message_id="10",
        text="НеНой, у меня день рождения 14 мая",
        metadata={"direct_mention": True},
    )

    result=pipeline.process(human_event)

    assert result.primary_action is PrimaryAction.REPLY
    assert len(birthday.calls) == 1
    assert mapper.calls == []
    assert context.calls[0]["action_state"]["birthday_profile"]["operation"] == "save"
