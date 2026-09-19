from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.services.context_builder import GenerationContext, GenerationMemory
from app_v2.services.response_generator import ResponseGenerationError, ResponseGenerator


class FakeAdapter:
    def __init__(self, text="Готово.", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def generate_text(self, role, input_text, **kwargs):
        self.calls.append((role, input_text, kwargs))
        if self.error:
            raise self.error
        usage = SimpleNamespace(model="fake-generator", usage_id="usage-1")
        return SimpleNamespace(text=self.text, usage=usage)


def _personality(mode):
    return {
        "mode": mode,
        "directness": 8,
        "brevity": 8,
        "warmth": 7,
        "pressure": 6,
        "humor": 5,
        "sarcasm": 4,
        "roast": 3,
        "profanity_level": 4,
        "profanity_frequency": 3,
        "initiative": 7,
        "callback": 8,
        "challenge": 8,
        "care": 8,
        "playfulness": 5,
        "sensitivity": 8,
        "ignored_internal_field": "must not be sent",
    }


def _context(
    scope_type=ScopeType.PERSONAL,
    mode=ResponseMode.ASSISTANT,
    memories=(),
    *,
    action_state=None,
):
    return GenerationContext(
        scope_type=scope_type,
        scope_id="u1" if scope_type is ScopeType.PERSONAL else "g1",
        event={"event_id": "evt1", "text": "Привет"},
        scene={"seriousness_score": 0.1},
        decision={
            "primary_action": PrimaryAction.REPLY.value,
            "mode": mode.value,
            "reason_codes": ["direct_mention"],
        },
        personality=_personality(mode.value),
        hot_messages=({"message_id": "1", "text": "Привет"},),
        memories=tuple(memories),
        target_user_id="u1",
        action_state=dict(action_state or {}),
        estimated_hot_tokens=20,
        estimated_memory_tokens=0,
    )


def receipts(*, memory_changed=False, reminder=None, task=None):
    memory = {
        "status": "succeeded",
        "changed": memory_changed,
        "written_ids": ["m1"] if memory_changed else [],
        "forgotten_ids": [],
    }
    task_receipt = task or {
        "status": "not_attempted",
        "changed": False,
        "entity_ids": [],
    }
    reminder_receipt = reminder or {
        "status": "not_attempted",
        "changed": False,
        "entity_ids": [],
        "operation": None,
    }
    return {"operation_receipts": {"memory": memory, "task": task_receipt, "reminder": reminder_receipt}}


def test_personal_prompt_selected_and_compact_personality_sent():
    adapter = FakeAdapter()
    generator = ResponseGenerator(adapter=adapter)

    result = generator.generate(_context())

    assert result.text == "Готово."
    _, input_text, kwargs = adapter.calls[0]
    payload = json.loads(input_text)
    assert "Personal Generator" in kwargs["instructions"]
    assert payload["decision"]["mode"] == "assistant"
    assert payload["personality"]["directness"] == 8
    assert "ignored_internal_field" not in payload["personality"]


def test_group_prompt_selected_for_group_context():
    adapter = FakeAdapter()
    generator = ResponseGenerator(adapter=adapter)

    generator.generate(_context(ScopeType.GROUP, ResponseMode.GROUP_BANTER))

    _, _, kwargs = adapter.calls[0]
    assert "Group Generator" in kwargs["instructions"]
    assert "не объясняй шутку" in kwargs["instructions"]
    assert "operation_receipts" in kwargs["instructions"]


def test_group_callback_without_memory_is_blocked_before_model_call():
    adapter = FakeAdapter()
    generator = ResponseGenerator(adapter=adapter)

    with pytest.raises(ResponseGenerationError, match="requires at least one"):
        generator.generate(_context(ScopeType.GROUP, ResponseMode.GROUP_CALLBACK, memories=()))

    assert adapter.calls == []


def test_group_callback_with_memory_passes_grounded_memory_to_model():
    memory = GenerationMemory(
        id="m1",
        memory_type="running_joke",
        summary="Серёга говорит «уже еду» до выезда.",
        confidence=0.95,
        importance=0.8,
        evidence=({"message_id": "90", "excerpt": "Уже еду"},),
    )
    adapter = FakeAdapter(text="58 минут. Крепкий мужик.")
    generator = ResponseGenerator(adapter=adapter)

    result = generator.generate(_context(ScopeType.GROUP, ResponseMode.GROUP_CALLBACK, memories=(memory,)))

    payload = json.loads(adapter.calls[0][1])
    assert result.text == "58 минут. Крепкий мужик."
    assert payload["memories"][0]["id"] == "m1"
    assert payload["memories"][0]["evidence"][0]["excerpt"] == "Уже еду"


def test_operation_receipts_are_passed_to_model_payload():
    action_state = receipts(memory_changed=True)
    adapter = FakeAdapter(text="В память зафиксировал.")
    ResponseGenerator(adapter=adapter).generate(_context(action_state=action_state))
    payload = json.loads(adapter.calls[0][1])
    assert payload["action_state"]["operation_receipts"]["memory"]["written_ids"] == ["m1"]
    assert payload["action_state"]["operation_receipts"]["task"]["status"] == "not_attempted"


def test_false_task_and_future_reminder_claims_are_replaced_with_truthful_status():
    adapter = FakeAdapter(text="Принял. Задачу создал, пну 21.10.")
    result = ResponseGenerator(adapter=adapter).generate(
        _context(action_state=receipts(memory_changed=True))
    )
    assert "В память зафиксировал" in result.text
    assert "Задачу не создавал" in result.text
    assert "Напоминание не ставил" in result.text
    assert "пну 21.10" not in result.text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Поставлю напоминание завтра.", "Напоминание не ставил"),
        ("Я тебе напомню завтра.", "Напоминание не ставил"),
        ("Задачу создам сейчас.", "Задачу не создавал"),
        ("Запомню это.", "В память это не записано"),
    ],
)
def test_future_bot_action_promises_are_blocked_without_receipt(text, expected):
    adapter = FakeAdapter(text=text)
    result = ResponseGenerator(adapter=adapter).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert expected in result.text
    assert result.text != text


@pytest.mark.parametrize(
    "text",
    [
        "Вася создал задачу вчера.",
        "Вася поставил напоминание себе на завтра.",
        "Пользователь сказал: «Задачу создал, напоминание поставлю сам».",
    ],
)
def test_third_party_or_quoted_action_statements_are_not_rewritten(text):
    adapter = FakeAdapter(text=text)
    result = ResponseGenerator(adapter=adapter).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert result.text == text


@pytest.mark.parametrize(
    "text",
    [
        "Задачу создал пользователь вчера.",
        "Напоминание поставил подрядчик.",
        "Запись в память сохранила сотрудница.",
    ],
)
def test_lowercase_role_third_party_action_facts_are_not_rewritten(text):
    result = ResponseGenerator(adapter=FakeAdapter(text=text)).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert result.text == text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Задачу создал я.", "Задачу не создавал"),
        ("Задача создана мной.", "Задачу не создавал"),
        ("Задача создана корректно.", "Задачу не создавал"),
        ("Напоминание поставлено мной.", "Напоминание не ставил"),
        ("Запись в память сохранена успешно.", "В память это не записано"),
        ("Запись из памяти удалена мной.", "Из памяти ничего не удал"),
    ],
)
def test_first_person_and_modifier_action_claims_require_receipts(text, expected):
    result = ResponseGenerator(adapter=FakeAdapter(text=text)).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert expected in result.text
    assert result.text != text


def test_false_memory_claim_is_blocked_when_nothing_was_written():
    adapter = FakeAdapter(text="Запомнил. Дальше разберёмся.")
    result = ResponseGenerator(adapter=adapter).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert "В память это не записано" in result.text
    assert result.text != adapter.text


def test_partial_memory_claim_reports_committed_part_without_full_success():
    action_state = {
        "operation_receipts": {
            "memory": {
                "status": "partial",
                "changed": True,
                "written_ids": ["m1"],
                "forgotten_ids": [],
            },
            "task": {"status": "not_attempted", "changed": False, "entity_ids": []},
            "reminder": {
                "status": "not_attempted",
                "changed": False,
                "entity_ids": [],
                "operation": None,
            },
        }
    }
    adapter = FakeAdapter(text="Запомнил всё.")
    result = ResponseGenerator(adapter=adapter).generate(_context(action_state=action_state))
    assert "Часть памяти записана" in result.text
    assert "операция завершилась не полностью" in result.text
    assert "В память это не записано" not in result.text
    assert result.text != adapter.text


def test_real_group_reminder_create_may_be_confirmed():
    reminder = {
        "status": "succeeded",
        "changed": True,
        "entity_ids": ["42"],
        "operation": "create",
    }
    adapter = FakeAdapter(text="Поставил напоминание. Буду пинать по расписанию.")
    result = ResponseGenerator(adapter=adapter).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=receipts(reminder=reminder),
        )
    )
    assert result.text == adapter.text


def test_zero_cancelled_reminders_cannot_be_reported_as_cancelled():
    reminder = {
        "status": "succeeded",
        "changed": False,
        "entity_ids": [],
        "operation": "cancel",
        "cancelled_count": 0,
    }
    adapter = FakeAdapter(text="Остановил напоминание.")
    result = ResponseGenerator(adapter=adapter).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=receipts(reminder=reminder),
        )
    )
    assert "Активное напоминание не остановлено" in result.text


def test_needs_clarification_never_turns_into_scheduled_claim():
    reminder = {
        "status": "needs_clarification",
        "changed": False,
        "entity_ids": [],
        "operation": "create",
    }
    adapter = FakeAdapter(text="Поставил напоминание.")
    result = ResponseGenerator(adapter=adapter).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=receipts(reminder=reminder),
        )
    )
    assert "Напоминание не поставлено" in result.text
    assert "уточнить время или адресата" in result.text


def test_generator_rejects_non_reply_decision():
    adapter = FakeAdapter()
    generator = ResponseGenerator(adapter=adapter)
    context = _context()
    context = GenerationContext(
        **{**context.__dict__, "decision": {"primary_action": "ignore", "mode": None, "reason_codes": []}}
    )

    with pytest.raises(ResponseGenerationError, match="primary_action=reply"):
        generator.generate(context)
    assert adapter.calls == []


def test_adapter_failure_is_wrapped_and_no_send_side_effect_exists():
    adapter = FakeAdapter(error=RuntimeError("boom"))
    generator = ResponseGenerator(adapter=adapter)

    with pytest.raises(ResponseGenerationError, match="generation failed"):
        generator.generate(_context())

    assert len(adapter.calls) == 1


def test_empty_model_text_is_rejected():
    generator = ResponseGenerator(adapter=FakeAdapter(text="   "))
    with pytest.raises(ResponseGenerationError, match="empty text"):
        generator.generate(_context())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("- Задача создана.", "Задачу не создавал"),
        ("Результат:\n- Напоминание поставлено.", "Напоминание не ставил"),
        ("Результат\nЗапись сохранена в памяти.", "В память это не записано"),
        ("1. Задача создана.", "Задачу не создавал"),
        ("* Напоминание поставлено.", "Напоминание не ставил"),
    ],
)
def test_operation_claims_at_line_and_markdown_list_boundaries_require_receipts(text, expected):
    result = ResponseGenerator(adapter=FakeAdapter(text=text)).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert expected in result.text
    assert result.text != text


@pytest.mark.parametrize(
    "text",
    [
        "- Задачу создал пользователь вчера.",
        "* Напоминание поставил подрядчик.",
        "1. Запись в память сохранила сотрудница.",
    ],
)
def test_markdown_list_third_party_action_facts_are_not_rewritten(text):
    result = ResponseGenerator(adapter=FakeAdapter(text=text)).generate(
        _context(action_state=receipts(memory_changed=False))
    )
    assert result.text == text



def _semantic_action_state(reminder):
    state = receipts(reminder=reminder)
    state["scheduled_action_interpretation"] = {
        "is_scheduled_action": True,
        "execution_kind": "generate_text",
        "instruction": "Удиви пользователя",
        "confidence": 0.97,
    }
    return state


@pytest.mark.parametrize(
    "model_text",
    [
        "Буду удивлять тебя каждый вечер.",
        "Буду писать тебе каждую минуту.",
        "Буду сообщать актуальный курс каждый день.",
    ],
)
def test_semantic_scheduled_action_false_promises_are_blocked_without_persistence(model_text):
    reminder = {
        "status": "failed",
        "changed": False,
        "entity_ids": [],
        "operation": "create",
        "reason": "unsupported_scheduled_capability",
    }

    result = ResponseGenerator(adapter=FakeAdapter(text=model_text)).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=_semantic_action_state(reminder),
        )
    )

    assert result.text != model_text
    assert "внешний источник данных пока не подключён" in result.text


def test_semantic_scheduled_action_timezone_clarification_is_deterministic():
    reminder = {
        "status": "needs_clarification",
        "changed": False,
        "entity_ids": [],
        "operation": "create",
        "reason": "timezone_required",
    }

    result = ResponseGenerator(adapter=FakeAdapter(text="Буду удивлять тебя каждый вечер.")).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=_semantic_action_state(reminder),
        )
    )

    assert "нужен часовой пояс" in result.text
    assert "Буду удивлять" not in result.text


def test_semantic_scheduled_action_promise_is_allowed_after_real_persistence():
    reminder = {
        "status": "succeeded",
        "changed": True,
        "entity_ids": ["42"],
        "operation": "create",
        "reason": "interpreted_scheduled_action",
    }
    model_text = "Буду удивлять тебя по расписанию."

    result = ResponseGenerator(adapter=FakeAdapter(text=model_text)).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=_semantic_action_state(reminder),
        )
    )

    assert result.text == model_text



def test_semantic_interpretation_does_not_override_confirmed_cancellation():
    reminder = {
        "status": "succeeded",
        "changed": True,
        "entity_ids": [],
        "operation": "cancel",
        "cancelled_count": 1,
        "reason": None,
    }
    state = _semantic_action_state(reminder)
    model_text = "Остановил ежедневные сюрпризы."

    result = ResponseGenerator(adapter=FakeAdapter(text=model_text)).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=state,
        )
    )

    assert result.text == model_text


def test_semantic_interpretation_preserves_zero_cancel_truth_guard():
    reminder = {
        "status": "succeeded",
        "changed": False,
        "entity_ids": [],
        "operation": "cancel",
        "cancelled_count": 0,
        "reason": "no_active_reminders",
    }
    state = _semantic_action_state(reminder)
    model_text = "Остановил напоминание."

    result = ResponseGenerator(adapter=FakeAdapter(text=model_text)).generate(
        _context(
            ScopeType.GROUP,
            ResponseMode.GROUP_DIRECT_REPLY,
            action_state=state,
        )
    )

    assert result.text != model_text
    assert "Активное напоминание не остановлено" in result.text
