from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.group_access import GroupAccessResult
from app_v2.services.connector_resolver import LegacyGroupConnectorResolver
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.scheduled_action_interpreter import ScheduledActionInterpretation


def _event() -> EventEnvelope:
    return EventEnvelope(
        event_id="evt-semantic-pipeline",
        event_type=EventType.DIRECT_MENTION,
        occurred_at=datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id="101",
        message_id="55",
        text="НеНой, каждые 30 минут удивляй меня",
        metadata={},
    )


class Access:
    def evaluate(self, event, *, now=None):
        return GroupAccessResult(
            True,
            "allowed",
            GroupContext(
                internal_chat_id=1,
                telegram_chat_id=event.scope_id,
                title="Тест",
                is_whitelisted=True,
                is_active=True,
                silent_until=None,
                profile={"profile": "friends", "initiative": 6},
                participant=ParticipantContext(
                    telegram_user_id=event.actor_user_id,
                    role="member",
                    profile={},
                ),
            ),
        )


class Interpreter:
    def __init__(self):
        self.calls = []

    def interpret(self, event):
        self.calls.append(event.event_id)
        return ScheduledActionInterpretation(
            is_scheduled_action=True,
            execution_kind="generate_text",
            instruction="Удиви пользователя новой короткой репликой",
            confidence=0.97,
        )


class Reminders:
    def __init__(self):
        self.received = None

    def cancel_on_response(self, event):
        return 0

    def maybe_schedule(self, event, *, now, interpreted_action=None):
        self.received = interpreted_action
        return SimpleNamespace(
            as_action_state=lambda: {
                "status": "scheduled",
                "reminder_id": 17,
                "recurring": True,
                "interval_seconds": 1800,
                "target_username": None,
                "due_at": "2026-09-19T18:30:00+00:00",
                "stop_on_reply": False,
                "cancelled_count": 0,
                "reason": "interpreted_scheduled_action",
                "timezone": None,
                "recurrence_rule": "interval:1800",
                "execution_kind": "generate_text",
                "action_instruction": interpreted_action.instruction,
            }
        )


class Scene:
    def analyze(self, event):
        return SceneAnalysis(direct_mention=True)


class Personality:
    def build(self, **kwargs):
        return {}


class ContextBuilder:
    def __init__(self):
        self.action_state = None

    def build(self, *, event, scene, decision, personality, subject_keys,
              memory_usage, callback_fatigue_minutes, action_state):
        self.action_state = action_state
        return SimpleNamespace(
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            memories=[],
        )


class Generator:
    def generate(self, context):
        return SimpleNamespace(text="Буду удивлять. Что ж, сами попросили.")


class Interventions:
    def record(self, **kwargs):
        return SimpleNamespace(id=9)


class Outbox:
    def enqueue(self, outbound):
        return 5, True


def test_group_pipeline_passes_semantic_action_to_scheduler_and_context():
    interpreter = Interpreter()
    reminders = Reminders()
    context_builder = ContextBuilder()
    pipeline = GroupPipeline(
        access_service=Access(),
        scene_analyzer=Scene(),
        personality_engine=Personality(),
        context_builder=context_builder,
        response_generator=Generator(),
        intervention_repo=Interventions(),
        outbox_repo=Outbox(),
        group_reminder_service=reminders,
        scheduled_action_interpreter=interpreter,
        connector_resolver=LegacyGroupConnectorResolver(),
    )

    result = pipeline.process(
        _event(),
        now=datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc),
    )

    assert result.generated_text == "Буду удивлять. Что ж, сами попросили."
    assert interpreter.calls == ["evt-semantic-pipeline"]
    assert reminders.received is not None
    assert reminders.received.execution_kind == "generate_text"
    assert reminders.received.instruction == "Удиви пользователя новой короткой репликой"
    assert context_builder.action_state["scheduled_action_interpretation"]["execution_kind"] == "generate_text"
    assert context_builder.action_state["group_reminder"]["status"] == "scheduled"
    connector_state = context_builder.action_state["connector"]
    assert connector_state["connector_type"] == "telegram_group"
    assert connector_state["preset"] == "friends"
    assert connector_state["memory"]["cross_connector_memory"] is False
    assert "-1001" not in repr(connector_state)
    receipt = context_builder.action_state["operation_receipts"]["reminder"]
    assert receipt["status"] == "succeeded"
    assert receipt["changed"] is True
    assert receipt["execution_kind"] == "generate_text"



class FailingConnectorResolver:
    def resolve(self, group_context):
        raise RuntimeError("connector store unavailable")


class CountingGenerator:
    def __init__(self):
        self.calls = 0

    def generate(self, context):
        self.calls += 1
        return SimpleNamespace(text="should not happen")


def test_connector_resolution_failure_fails_closed_before_generation():
    generator = CountingGenerator()
    interpreter = Interpreter()
    reminders = Reminders()
    pipeline = GroupPipeline(
        access_service=Access(),
        scene_analyzer=Scene(),
        personality_engine=Personality(),
        context_builder=ContextBuilder(),
        response_generator=generator,
        intervention_repo=Interventions(),
        outbox_repo=Outbox(),
        group_reminder_service=reminders,
        scheduled_action_interpreter=interpreter,
        connector_resolver=FailingConnectorResolver(),
    )

    result = pipeline.process(
        _event(),
        now=datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc),
    )

    assert result.primary_action is not None
    assert result.primary_action.value == "ignore"
    assert result.access_reason == "connector_unavailable"
    assert generator.calls == 0
    assert interpreter.calls == []
    assert reminders.received is None
