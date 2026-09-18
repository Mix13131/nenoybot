from __future__ import annotations

from types import SimpleNamespace

from app_v2.domain.enums import MemoryStatus
from app_v2.services.operation_receipts import (
    group_reminder_receipt,
    memory_receipt,
    personal_operation_receipts,
)


def card(memory_id: str, status=MemoryStatus.ACTIVE):
    return SimpleNamespace(id=memory_id, status=status)


def mapper_result(*, written=(), forgotten=(), failed=False, reason=None):
    return SimpleNamespace(
        written=tuple(written),
        forgotten_ids=tuple(forgotten),
        failed=failed,
        reason=reason,
    )


def test_memory_receipt_confirms_only_real_persisted_ids() -> None:
    receipt = memory_receipt(
        mapper_result(written=(card("m1"), card("m2", MemoryStatus.CANDIDATE))),
        attempted=True,
    )
    assert receipt["status"] == "succeeded"
    assert receipt["changed"] is True
    assert receipt["written_ids"] == ["m1", "m2"]
    assert receipt["active_count"] == 1
    assert receipt["candidate_count"] == 1


def test_memory_mapper_success_without_write_is_not_a_save() -> None:
    receipt = memory_receipt(mapper_result(), attempted=True)
    assert receipt["status"] == "succeeded"
    assert receipt["changed"] is False
    assert receipt["written_ids"] == []


def test_memory_failure_and_missing_context_are_not_successful_changes() -> None:
    failed = memory_receipt(
        mapper_result(failed=True, reason="mapper_failure:RuntimeError"),
        attempted=True,
    )
    missing = memory_receipt(
        mapper_result(reason="insufficient_context"),
        attempted=True,
    )
    assert failed["status"] == "failed" and failed["changed"] is False
    assert missing["status"] == "needs_clarification" and missing["changed"] is False


def test_memory_failure_after_committed_write_is_partial_not_false_failure_noop() -> None:
    receipt = memory_receipt(
        mapper_result(
            written=(card("m1"),),
            failed=True,
            reason="mapper_failure:RuntimeError",
        ),
        attempted=True,
    )
    assert receipt["status"] == "partial"
    assert receipt["changed"] is True
    assert receipt["written_ids"] == ["m1"]
    assert receipt["active_count"] == 1


def test_personal_task_and_reminder_are_explicitly_not_attempted() -> None:
    receipts = personal_operation_receipts(mapper_result(written=(card("m1"),)))
    assert receipts["memory"]["changed"] is True
    assert receipts["task"] == {
        "status": "not_attempted",
        "changed": False,
        "entity_ids": [],
        "reason": "conversation_task_action_not_wired",
    }
    assert receipts["reminder"]["status"] == "not_attempted"
    assert receipts["reminder"]["changed"] is False


def test_group_scheduled_reminder_requires_persisted_id() -> None:
    good = group_reminder_receipt({
        "status": "scheduled",
        "reminder_id": 42,
        "due_at": "2026-09-17T10:00:00+00:00",
        "target_username": "friend",
    })
    broken = group_reminder_receipt({"status": "scheduled", "reminder_id": None})
    assert good["status"] == "succeeded"
    assert good["changed"] is True
    assert good["entity_ids"] == ["42"]
    assert good["operation"] == "create"
    assert broken["status"] == "partial"
    assert broken["changed"] is False


def test_group_cancel_zero_is_not_reported_as_actual_cancellation() -> None:
    missing = group_reminder_receipt({
        "status": "not_cancelled",
        "cancelled_count": 0,
        "reason": "no_active_reminders",
    })
    actual = group_reminder_receipt({
        "status": "cancelled",
        "cancelled_count": 2,
    })
    assert missing["status"] == "succeeded"
    assert missing["changed"] is False
    assert missing["cancelled_count"] == 0
    assert actual["status"] == "succeeded"
    assert actual["changed"] is True
    assert actual["cancelled_count"] == 2
    assert actual["operation"] == "cancel"


def test_not_scheduled_and_error_reminders_are_not_claimable_success() -> None:
    clarify = group_reminder_receipt({
        "status": "not_scheduled",
        "reason": "unsupported_time_expression",
    })
    failed = group_reminder_receipt({"status": "error", "reason": "RuntimeError"})
    assert clarify["status"] == "needs_clarification"
    assert clarify["changed"] is False
    assert failed["status"] == "failed"
    assert failed["changed"] is False


def test_ambiguous_forget_receipt_needs_clarification():
    from app_v2.services.memory_mapper import MapperResult
    receipt = memory_receipt(MapperResult(reason="forget_target_ambiguous"), attempted=True)
    assert receipt["status"] == "needs_clarification"
    assert receipt["changed"] is False
