from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.group_pipeline import GroupPipeline


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def event(excerpt="старый контекст группы") -> EventEnvelope:
    return EventEnvelope(
        event_id="silence:test:1",
        event_type=EventType.GROUP_SILENCE_WAKEUP,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id=None,
        message_id=None,
        text=None,
        metadata={
            "synthetic": True,
            "silence_wakeup": True,
            "silence_minutes": 240,
            "last_human_message_id": 77,
            "last_human_excerpt": excerpt,
        },
    )


class Access:
    def evaluate(self, evt, *, now=None):
        return GroupAccessResult(
            True,
            "allowed",
            GroupContext(
                internal_chat_id=1,
                telegram_chat_id=evt.scope_id,
                title="Friends",
                is_whitelisted=True,
                is_active=True,
                silent_until=None,
                profile={"profile": "friends", "initiative": 3},
                participant=ParticipantContext(telegram_user_id=None),
            ),
        )


class WakeupGuard:
    def __init__(self, current=True):
        self.current = current
        self.calls = []

    def is_current_episode(self, scope_id, last_human_message_id):
        self.calls.append((scope_id, last_human_message_id))
        return self.current


class Scene:
    def __init__(self, result=None):
        self.result = result or SceneAnalysis()
        self.calls = []

    def analyze(self, evt, *, recent_context=None):
        self.calls.append((evt.event_id, recent_context))
        return self.result


class Personality:
    def build(self, **kwargs):
        return {}


class ContextBuilder:
    def build(self, *, event, scene, decision, personality, subject_keys,
              memory_usage, callback_fatigue_minutes, action_state):
        return SimpleNamespace(
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            memories=[],
        )


class Generator:
    def __init__(self):
        self.calls = 0

    def generate(self, context):
        self.calls += 1
        return SimpleNamespace(text="Так, вопрос на миллион: кто сегодня уже успел переобуться?")


class Interventions:
    def __init__(self):
        self.rows = []

    def record(self, **kwargs):
        self.rows.append(kwargs)
        return SimpleNamespace(id=len(self.rows))


class Outbox:
    def __init__(self):
        self.items = []

    def enqueue(self, outbound):
        self.items.append(outbound)
        return 5, True


def build(scene, *, guard=None):
    generator = Generator()
    interventions = Interventions()
    outbox = Outbox()
    pipeline = GroupPipeline(
        access_service=Access(),
        scene_analyzer=scene,
        personality_engine=Personality(),
        context_builder=ContextBuilder(),
        response_generator=generator,
        intervention_repo=interventions,
        outbox_repo=outbox,
        silence_wakeup_guard=guard or WakeupGuard(),
        unsolicited_enabled=True,
    )
    return pipeline, generator, interventions, outbox


def test_silence_wakeup_passes_last_human_excerpt_to_scene_analyzer():
    scene = Scene()
    pipeline, generator, _, outbox = build(scene)

    result = pipeline.process(event("последняя человеческая реплика"), now=NOW)

    assert scene.calls == [("silence:test:1", "последняя человеческая реплика")]
    assert result.primary_action is PrimaryAction.REPLY
    assert result.mode is ResponseMode.GROUP_BANTER
    assert generator.calls == 1
    assert len(outbox.items) == 1


def test_serious_last_scene_suppresses_silence_wakeup_before_generation():
    scene = Scene(SceneAnalysis(seriousness_score=0.9))
    pipeline, generator, interventions, outbox = build(scene)

    result = pipeline.process(event("тяжёлая тема"), now=NOW)

    assert result.primary_action is PrimaryAction.IGNORE
    assert generator.calls == 0
    assert outbox.items == []
    assert interventions.rows[-1]["generated_text"] is None



def test_historical_question_cannot_turn_silence_wakeup_into_explicit_bypass():
    scene = Scene(SceneAnalysis(question_to_bot=True, command_intent="mute"))
    pipeline, generator, _, outbox = build(scene)

    result = pipeline.process(event("НеНой, ты тут?"), now=NOW)

    assert result.primary_action is PrimaryAction.REPLY
    assert result.mode is ResponseMode.GROUP_BANTER
    assert generator.calls == 1
    assert len(outbox.items) == 1



def test_new_human_message_makes_queued_silence_wakeup_stale():
    scene = Scene()
    guard = WakeupGuard(current=False)
    pipeline, generator, interventions, outbox = build(scene, guard=guard)

    result = pipeline.process(event("старый контекст"), now=NOW)

    assert result.primary_action is PrimaryAction.IGNORE
    assert result.access_reason == "silence_wakeup_stale"
    assert guard.calls == [("-100777", 77)]
    assert scene.calls == []
    assert generator.calls == 0
    assert interventions.rows == []
    assert outbox.items == []
