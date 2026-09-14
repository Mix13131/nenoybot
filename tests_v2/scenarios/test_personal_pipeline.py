from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app_v2.domain.enums import EventType, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.services.context_builder import GenerationContext, GenerationMemory
from app_v2.services.memory_mapper import MapperResult
from app_v2.services.personal_pipeline import PersonalPipeline, PersonalPipelineError
from app_v2.services.personality_engine import PersonalityEngine


def evt(text="Привет", *, event_id="evt1", metadata=None, scope=ScopeType.PERSONAL):
    return EventEnvelope(
        event_id=event_id,
        event_type=EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope,
        scope_id="u1" if scope is ScopeType.PERSONAL else "g1",
        actor_user_id="u1",
        message_id="100",
        text=text,
        metadata=metadata or {},
    )


class FakeSceneAnalyzer:
    def __init__(self, scene=None):
        self.scene = scene or SceneAnalysis()
    def analyze(self, event):
        return self.scene


class FakeMapper:
    def __init__(self, result=None):
        self.result = result or MapperResult()
        self.calls = []
    def map_event(self, event, *, target_memory_ids=()):
        self.calls.append((event, tuple(target_memory_ids)))
        return self.result


class FakeContextBuilder:
    def __init__(self, memories=()):
        self.memories = tuple(memories)
        self.calls = []
    def build(self, **kwargs):
        self.calls.append(kwargs)
        event = kwargs["event"]
        decision = kwargs["decision"]
        personality = kwargs["personality"]
        return GenerationContext(
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            event=event.model_dump(mode="json"),
            scene=kwargs["scene"].model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),
            personality=personality.model_dump(mode="json"),
            hot_messages=(),
            memories=self.memories,
            target_user_id=decision.target_user_id,
            action_state={},
            estimated_hot_tokens=0,
            estimated_memory_tokens=20 if self.memories else 0,
        )


class FakeGenerator:
    def __init__(self, text="Ответ", error=None):
        self.text = text
        self.error = error
        self.calls = []
    def generate(self, context):
        self.calls.append(context)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, model="fake", usage_id="u1")


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
        outbox_id=len(self.by_key)+1
        self.by_key[message.dedupe_key]=outbox_id
        return outbox_id, True


def pipeline(*, scene=None, mapper=None, memories=(), generator=None, outbox=None):
    return PersonalPipeline(
        scene_analyzer=FakeSceneAnalyzer(scene),
        memory_mapper=mapper or FakeMapper(),
        personality_engine=PersonalityEngine(),
        context_builder=FakeContextBuilder(memories),
        response_generator=generator or FakeGenerator(),
        intervention_repo=FakeInterventions(),
        outbox_repo=outbox or FakeOutbox(),
    )


def test_ordinary_conversation_assistant_to_outbox():
    p=pipeline()
    result=p.process(evt())
    assert result.mode is ResponseMode.ASSISTANT
    assert result.generated_text == "Ответ"
    assert result.outbox_created is True


def test_commitment_routes_to_coach():
    p=pipeline(scene=SceneAnalysis(commitment_signal=0.9))
    assert p.process(evt("Сделаю сегодня")).mode is ResponseMode.COACH


def test_contradiction_routes_to_mirror_and_callback_retrieval():
    p=pipeline(scene=SceneAnalysis(contradiction_score=0.9))
    result=p.process(evt("Я же не переобувался"))
    assert result.mode is ResponseMode.MIRROR
    assert p.context_builder.calls[0]["memory_usage"] == "callback"


def test_sensitive_message_routes_to_care():
    p=pipeline(scene=SceneAnalysis(sensitivity_score=0.9))
    assert p.process(evt("Мне тяжело")).mode is ResponseMode.CARE


def test_explicit_remember_result_is_exposed():
    card=SimpleNamespace(id="mem1")
    mapper=FakeMapper(MapperResult(written=(card,), reason="explicit_remember"))
    result=pipeline(mapper=mapper).process(evt("Запомни: важная штука"))
    assert result.memory_written_ids == ("mem1",)


def test_explicit_forget_targets_flow_from_event_metadata():
    mapper=FakeMapper(MapperResult(forgotten_ids=("mem1",), reason="explicit_forget"))
    result=pipeline(mapper=mapper).process(evt("забудь это", metadata={"target_memory_ids":["mem1"]}))
    assert mapper.calls[0][1] == ("mem1",)
    assert result.memory_forgotten_ids == ("mem1",)


def test_relevant_callback_memory_reaches_generator():
    memory=GenerationMemory(id="m1",memory_type="fact",summary="Пользователь уже обещал это",confidence=.95,importance=.8,evidence=())
    p=pipeline(memories=(memory,))
    p.process(evt())
    assert p.response_generator.calls[0].memories[0].id == "m1"


def test_generation_failure_logs_but_does_not_enqueue():
    outbox=FakeOutbox()
    p=pipeline(generator=FakeGenerator(error=RuntimeError("boom")),outbox=outbox)
    result=p.process(evt())
    assert result.generation_failed is True
    assert result.outbox_id is None
    assert outbox.calls == []
    assert p.intervention_repo.rows[-1]["generated_text"] is None


def test_group_event_is_rejected_by_personal_pipeline():
    p=pipeline()
    with pytest.raises(PersonalPipelineError):
        p.process(evt(scope=ScopeType.GROUP))


def test_same_event_retry_does_not_create_duplicate_outbox():
    outbox=FakeOutbox()
    p=pipeline(outbox=outbox)
    first=p.process(evt(event_id="same"))
    second=p.process(evt(event_id="same"))
    assert first.outbox_id == second.outbox_id
    assert first.outbox_created is True
    assert second.outbox_created is False
    assert len(outbox.by_key) == 1
