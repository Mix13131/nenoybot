from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app_v2.domain.enums import EventType, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.context_builder import GenerationContext
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.group_pipeline import GroupPipeline, GroupPipelineError
from app_v2.services.personality_engine import PersonalityEngine


def event(event_type=EventType.GROUP_MESSAGE, *, event_id="g1", text="болтовня"):
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="55",
        text=text,
        metadata={"reply_to_bot": event_type is EventType.REPLY_TO_BOT},
    )


def group_context() -> GroupContext:
    return GroupContext(
        internal_chat_id=7,
        telegram_chat_id="-100777",
        title="Friends",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile={"profile": "friends", "profanity_level": 8, "profanity_frequency": 5},
        participant=ParticipantContext(
            telegram_user_id="123",
            display_name="Anton",
            role="member",
            profile={"personality_modifiers": {"warmth": 1}},
        ),
    )


class FakeAccess:
    def __init__(self, allowed=True, reason="allowed"):
        self.result = GroupAccessResult(allowed, reason, group_context() if allowed else None)
        self.calls = []
    def evaluate(self, event, *, now=None):
        self.calls.append((event, now))
        return self.result


class FakeSceneAnalyzer:
    def __init__(self, scene=None):
        self.scene = scene or SceneAnalysis()
        self.calls = []
    def analyze(self, event):
        self.calls.append(event)
        return self.scene


class FakeContextBuilder:
    def __init__(self, *, scope_type=ScopeType.GROUP, memories=()):
        self.scope_type = scope_type
        self.memories = tuple(memories)
        self.calls = []
    def build(self, **kwargs):
        self.calls.append(kwargs)
        event = kwargs["event"]
        decision = kwargs["decision"]
        personality = kwargs["personality"]
        return GenerationContext(
            scope_type=self.scope_type,
            scope_id=event.scope_id if self.scope_type is ScopeType.GROUP else event.actor_user_id,
            event=event.model_dump(mode="json"),
            scene=kwargs["scene"].model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),
            personality=personality.model_dump(mode="json"),
            hot_messages=(),
            memories=self.memories,
            target_user_id=decision.target_user_id,
            action_state=kwargs.get("action_state", {}),
            estimated_hot_tokens=0,
            estimated_memory_tokens=0,
        )


class FakeGenerator:
    def __init__(self, text="Групповой ответ", error=None):
        self.text = text
        self.error = error
        self.calls = []
    def generate(self, context):
        self.calls.append(context)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


class FakeInterventions:
    def __init__(self): self.rows=[]
    def record(self, **kwargs):
        self.rows.append(kwargs)
        return SimpleNamespace(id=len(self.rows))


class FakeOutbox:
    def __init__(self): self.by_key={}; self.calls=[]
    def enqueue(self, message):
        self.calls.append(message)
        if message.dedupe_key in self.by_key:
            return self.by_key[message.dedupe_key], False
        oid=len(self.by_key)+1
        self.by_key[message.dedupe_key]=oid
        return oid, True


class FakeMemoryMapper:
    def __init__(self, memory_ids=("mapped-1",)):
        self.memory_ids=tuple(memory_ids)
        self.calls=[]
    def map_event(self, event, **kwargs):
        self.calls.append((event, kwargs))
        return SimpleNamespace(
            written=tuple(SimpleNamespace(id=item) for item in self.memory_ids),
            forgotten_ids=(),
            failed=False,
        )


def pipeline(*, access=None, scene=None, context=None, generator=None, outbox=None, mapper=None):
    return GroupPipeline(
        access_service=access or FakeAccess(),
        scene_analyzer=FakeSceneAnalyzer(scene),
        personality_engine=PersonalityEngine(),
        context_builder=context or FakeContextBuilder(),
        response_generator=generator or FakeGenerator(),
        intervention_repo=FakeInterventions(),
        outbox_repo=outbox or FakeOutbox(),
        memory_mapper=mapper,
        unsolicited_enabled=False,
    )


def test_ordinary_group_chat_is_silent() -> None:
    p=pipeline()
    result=p.process(event())
    assert result.allowed is True
    assert result.primary_action is PrimaryAction.IGNORE
    assert result.outbox_id is None
    assert p.response_generator.calls == []


def test_low_signal_ordinary_group_message_does_not_trigger_mapper() -> None:
    mapper = FakeMemoryMapper()
    pipeline(mapper=mapper).process(event(text="ок"))
    assert mapper.calls == []


def test_low_signal_group_edit_always_reaches_mapper_reconciliation() -> None:
    mapper = FakeMemoryMapper(memory_ids=())
    pipeline(mapper=mapper).process(event(EventType.EDITED_MESSAGE, text="ок"))
    assert len(mapper.calls) == 1
    assert mapper.calls[0][0].event_type is EventType.EDITED_MESSAGE


def test_direct_mention_replies_despite_silence_first() -> None:
    p=pipeline()
    result=p.process(event(EventType.DIRECT_MENTION, text="@nenoy что думаешь?"))
    assert result.primary_action is PrimaryAction.REPLY
    assert result.mode is ResponseMode.GROUP_DIRECT_REPLY
    assert result.outbox_created is True
    assert result.generated_text == "Групповой ответ"


def test_reply_to_bot_replies() -> None:
    p=pipeline()
    result=p.process(event(EventType.REPLY_TO_BOT, text="а вот тут?"))
    assert result.primary_action is PrimaryAction.REPLY
    assert result.outbox_created is True


def test_serious_ordinary_group_message_stays_silent() -> None:
    p=pipeline(scene=SceneAnalysis(seriousness_score=.95, conflict_score=.9, roast_opportunity=1.0))
    result=p.process(event(text="реальный конфликт"))
    assert result.primary_action is PrimaryAction.IGNORE
    assert p.response_generator.calls == []


def test_non_whitelisted_group_stops_before_analysis() -> None:
    access=FakeAccess(False, "group_not_whitelisted")
    analyzer=FakeSceneAnalyzer()
    p=GroupPipeline(
        access_service=access,
        scene_analyzer=analyzer,
        personality_engine=PersonalityEngine(),
        context_builder=FakeContextBuilder(),
        response_generator=FakeGenerator(),
        intervention_repo=FakeInterventions(),
        outbox_repo=FakeOutbox(),
    )
    result=p.process(event())
    assert result.allowed is False
    assert result.access_reason == "group_not_whitelisted"
    assert analyzer.calls == []


def test_group_context_builder_is_called_with_group_scope_subject_and_profile() -> None:
    context=FakeContextBuilder()
    p=pipeline(context=context)
    p.process(event(EventType.DIRECT_MENTION))
    call=context.calls[0]
    assert call["event"].scope_type is ScopeType.GROUP
    assert call["subject_keys"] == ["user:123"]
    assert call["memory_usage"] == "assist"
    assert call["action_state"]["group_title"] == "Friends"


def test_cross_scope_context_is_rejected() -> None:
    p=pipeline(context=FakeContextBuilder(scope_type=ScopeType.PERSONAL))
    with pytest.raises(GroupPipelineError, match="cross-scope"):
        p.process(event(EventType.DIRECT_MENTION))


def test_same_group_event_retry_does_not_duplicate_outbox() -> None:
    outbox=FakeOutbox()
    p=pipeline(outbox=outbox)
    first=p.process(event(EventType.DIRECT_MENTION, event_id="same-g"))
    second=p.process(event(EventType.DIRECT_MENTION, event_id="same-g"))
    assert first.outbox_id == second.outbox_id
    assert first.outbox_created is True
    assert second.outbox_created is False
    assert len(outbox.by_key) == 1


def test_generation_failure_never_enqueues_message() -> None:
    outbox=FakeOutbox()
    p=pipeline(generator=FakeGenerator(error=RuntimeError("boom")), outbox=outbox)
    result=p.process(event(EventType.DIRECT_MENTION))
    assert result.generation_failed is True
    assert result.outbox_id is None
    assert outbox.calls == []


def test_meaningful_group_message_maps_memory_even_when_bot_stays_silent() -> None:
    mapper=FakeMemoryMapper(("group-memory-1",))
    p=pipeline(scene=SceneAnalysis(memory_value=.8), mapper=mapper)

    result=p.process(event(text="мы решили ехать в субботу"))

    assert result.primary_action is PrimaryAction.IGNORE
    assert result.mapped_memory_ids == ("group-memory-1",)
    assert len(mapper.calls) == 1
    assert p.response_generator.calls == []
    assert p.intervention_repo.rows[-1]["extra_metadata"]["mapped_memory_ids"] == ["group-memory-1"]


def test_low_value_group_chatter_does_not_call_memory_mapper() -> None:
    mapper=FakeMemoryMapper()
    p=pipeline(scene=SceneAnalysis(memory_value=.1, banter_score=.2), mapper=mapper)

    result=p.process(event(text="ага"))

    assert result.primary_action is PrimaryAction.IGNORE
    assert result.mapped_memory_ids == ()
    assert mapper.calls == []
