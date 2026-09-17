from __future__ import annotations

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.response_generator import ResponseGenerator


def _receipts(*, memory=None, task=None, reminder=None):
    return {
        "operation_receipts": {
            "memory": memory or {
                "status": "succeeded",
                "changed": False,
                "written_ids": [],
                "forgotten_ids": [],
            },
            "task": task or {
                "status": "not_attempted",
                "changed": False,
                "entity_ids": [],
            },
            "reminder": reminder or {
                "status": "not_attempted",
                "changed": False,
                "entity_ids": [],
                "operation": None,
            },
        }
    }


def test_passive_success_claims_require_real_receipts() -> None:
    state = _receipts()

    assert "Задачу не создавал" in ResponseGenerator._enforce_operation_receipts(
        "Задача создана.", state
    )
    assert "Напоминание не ставил" in ResponseGenerator._enforce_operation_receipts(
        "Напоминание поставлено.", state
    )
    assert "Из памяти ничего не удалял" in ResponseGenerator._enforce_operation_receipts(
        "Запись из памяти удалена.", state
    )


def test_passive_claims_are_allowed_with_matching_real_receipts() -> None:
    state = _receipts(
        memory={
            "status": "succeeded",
            "changed": True,
            "written_ids": [],
            "forgotten_ids": ["m1"],
        },
        task={
            "status": "succeeded",
            "changed": True,
            "entity_ids": ["t1"],
        },
        reminder={
            "status": "succeeded",
            "changed": True,
            "entity_ids": ["r1"],
            "operation": "create",
        },
    )

    assert ResponseGenerator._enforce_operation_receipts("Задача создана.", state) == "Задача создана."
    assert ResponseGenerator._enforce_operation_receipts("Напоминание поставлено.", state) == "Напоминание поставлено."
    assert ResponseGenerator._enforce_operation_receipts("Запись из памяти удалена.", state) == "Запись из памяти удалена."


def test_object_first_third_party_facts_are_not_rewritten() -> None:
    state = _receipts()
    assert ResponseGenerator._enforce_operation_receipts(
        "Задачу создал Вася вчера.", state
    ) == "Задачу создал Вася вчера."
    assert ResponseGenerator._enforce_operation_receipts(
        "Напоминание поставила Оля утром.", state
    ) == "Напоминание поставила Оля утром."


def test_group_target_memory_ids_are_taken_only_from_current_event_metadata() -> None:
    event = EventEnvelope(
        event_id="tg:501",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at="2026-09-17T09:00:00+00:00",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="11",
        message_id="501",
        text="забудь это",
        metadata={"target_memory_ids": ["mem-a", "", 42]},
    )
    assert GroupPipeline._target_memory_ids(event) == ("mem-a", "42")

    event_without_targets = event.model_copy(update={"metadata": {}})
    assert GroupPipeline._target_memory_ids(event_without_targets) == ()
