from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.scheduled_action_interpreter import ScheduledActionInterpreter


def _event(text: str, event_type: EventType = EventType.DIRECT_MENTION) -> EventEnvelope:
    return EventEnvelope(
        event_id="evt-action-1",
        event_type=event_type,
        occurred_at=datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id="101",
        message_id="55",
        reply_to_message_id=None,
        text=text,
        metadata={},
    )


class FakeAdapter:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = []

    def generate_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=self.payload)


def test_temporal_gate_is_about_time_not_action_verbs():
    adapter = FakeAdapter({
        "is_scheduled_action": True,
        "execution_kind": "generate_text",
        "instruction": "Удиви пользователя",
        "confidence": 0.96,
    })
    interpreter = ScheduledActionInterpreter(adapter, instructions="classify")

    result = interpreter.interpret(
        _event("НеНой, каждый день в 20:00 удивляй меня")
    )

    assert result is not None
    assert result.execution_kind == "generate_text"
    assert result.instruction == "Удиви пользователя"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1]["schema_name"] == "scheduled_action_interpretation"


def test_non_temporal_message_does_not_spend_classifier_call():
    adapter = FakeAdapter({
        "is_scheduled_action": True,
        "execution_kind": "generate_text",
        "instruction": "Удиви пользователя",
        "confidence": 0.99,
    })
    interpreter = ScheduledActionInterpreter(adapter, instructions="classify")

    assert interpreter.interpret(_event("НеНой, удиви меня прямо сейчас")) is None
    assert adapter.calls == []


def test_statement_about_future_is_not_treated_as_action_when_model_says_no():
    adapter = FakeAdapter({
        "is_scheduled_action": False,
        "execution_kind": "none",
        "instruction": "",
        "confidence": 0.94,
    })
    interpreter = ScheduledActionInterpreter(adapter, instructions="classify")

    assert interpreter.interpret(_event("НеНой, сегодня мы встречаемся в 20:00")) is None


def test_external_data_capability_is_preserved_for_policy_layer():
    adapter = FakeAdapter({
        "is_scheduled_action": True,
        "execution_kind": "external_data",
        "instruction": "Сообщи актуальный курс доллара",
        "confidence": 0.97,
    })
    interpreter = ScheduledActionInterpreter(adapter, instructions="classify")

    result = interpreter.interpret(
        _event("НеНой, каждый день в 9 сообщай актуальный курс доллара")
    )

    assert result is not None
    assert result.execution_kind == "external_data"


def test_classifier_failure_fails_closed():
    interpreter = ScheduledActionInterpreter(
        FakeAdapter(error=TimeoutError("classifier timeout")),
        instructions="classify",
    )

    assert interpreter.interpret(
        _event("НеНой, каждые 30 минут развлекай меня")
    ) is None


def test_low_confidence_interpretation_fails_closed():
    adapter = FakeAdapter({
        "is_scheduled_action": True,
        "execution_kind": "generate_text",
        "instruction": "Развлеки пользователя",
        "confidence": 0.41,
    })
    interpreter = ScheduledActionInterpreter(adapter, instructions="classify")

    assert interpreter.interpret(
        _event("НеНой, каждые 30 минут развлекай меня")
    ) is None
