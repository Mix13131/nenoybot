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

    for text in ("Задача создана.", "Задачи созданы."):
        assert "Задачу не создавал" in ResponseGenerator._enforce_operation_receipts(text, state)
    for text in ("Напоминание поставлено.", "Напоминания поставлены."):
        assert "Напоминание не ставил" in ResponseGenerator._enforce_operation_receipts(text, state)
    for text in ("Запись из памяти удалена.", "Записи из памяти удалены."):
        assert "Из памяти ничего не удалял" in ResponseGenerator._enforce_operation_receipts(text, state)
    assert "В память это не записано" in ResponseGenerator._enforce_operation_receipts(
        "Запись сохранена в памяти.", state
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
    for text in (
        "Задачу создал Вася вчера.",
        "Задачу создал пользователь вчера.",
        "Задачу создал наш менеджер вчера.",
        "Напоминание поставил подрядчик.",
        "Напоминание поставила Оля утром.",
    ):
        assert ResponseGenerator._enforce_operation_receipts(text, state) == text


def test_object_first_bot_claim_without_receipt_is_still_blocked() -> None:
    state = _receipts()
    assert "Задачу не создавал" in ResponseGenerator._enforce_operation_receipts(
        "Задачу создал вчера.", state
    )
    assert "Напоминание не ставил" in ResponseGenerator._enforce_operation_receipts(
        "Напоминание поставил на завтра.", state
    )


class FakeInterventionRepo:
    def __init__(self, resolved=()):
        self.resolved = tuple(resolved)
        self.calls = []

    def selected_memory_ids_for_bot_message(self, **kwargs):
        self.calls.append(kwargs)
        return self.resolved


def _pipeline_with_interventions(repo):
    pipeline = object.__new__(GroupPipeline)
    pipeline.intervention_repo = repo
    return pipeline


def _forget_event(*, metadata=None, reply_to="700"):
    return EventEnvelope(
        event_id="tg:501",
        event_type=EventType.REPLY_TO_BOT,
        occurred_at="2026-09-17T09:00:00+00:00",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="11",
        message_id="501",
        reply_to_message_id=reply_to,
        text="забудь это",
        metadata=metadata or {},
    )


def test_group_target_memory_ids_prefer_explicit_current_event_metadata() -> None:
    repo = FakeInterventionRepo(("from-reply",))
    pipeline = _pipeline_with_interventions(repo)
    event = _forget_event(metadata={"target_memory_ids": ["mem-a", "", 42]})
    assert pipeline._target_memory_ids(event) == ("mem-a", "42")
    assert repo.calls == []


def test_group_forget_does_not_delete_reply_retrieval_context() -> None:
    repo = FakeInterventionRepo(("mem-a", "mem-b"))
    pipeline = _pipeline_with_interventions(repo)
    event = _forget_event()
    assert pipeline._target_memory_ids(event) == ()
    assert repo.calls == []


def test_group_forget_without_resolvable_reply_is_safe_noop() -> None:
    repo = FakeInterventionRepo(())
    pipeline = _pipeline_with_interventions(repo)
    assert pipeline._target_memory_ids(_forget_event(reply_to=None)) == ()


def test_group_forget_single_reply_context_is_still_not_proven_target() -> None:
    repo = FakeInterventionRepo(("mem-a",))
    pipeline = _pipeline_with_interventions(repo)
    assert pipeline._target_memory_ids(_forget_event()) == ()
    assert repo.calls == []
