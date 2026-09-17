from __future__ import annotations

from typing import Any


_NOT_WIRED_TASK_REASON = "conversation_task_action_not_wired"
_NOT_WIRED_REMINDER_REASON = "conversation_reminder_action_not_wired"


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def memory_receipt(mapper_result: Any | None, *, attempted: bool) -> dict[str, Any]:
    if not attempted or mapper_result is None:
        return {
            "status": "not_attempted",
            "changed": False,
            "written_ids": [],
            "forgotten_ids": [],
            "active_count": 0,
            "candidate_count": 0,
            "reason": "memory_mapping_not_selected",
        }

    written = tuple(getattr(mapper_result, "written", ()) or ())
    forgotten = tuple(getattr(mapper_result, "forgotten_ids", ()) or ())
    failed = bool(getattr(mapper_result, "failed", False))
    reason = getattr(mapper_result, "reason", None)

    active_count = sum(1 for card in written if _value(getattr(card, "status", "")) == "active")
    candidate_count = sum(1 for card in written if _value(getattr(card, "status", "")) == "candidate")
    written_ids = [str(getattr(card, "id")) for card in written if getattr(card, "id", None)]
    forgotten_ids = [str(item) for item in forgotten if str(item).strip()]
    changed = bool(written_ids or forgotten_ids)

    if failed and changed:
        status = "partial"
    elif failed:
        status = "failed"
        changed = False
    elif changed:
        status = "succeeded"
    elif reason in {"insufficient_context", "forget_target_ambiguous"}:
        status = "needs_clarification"
    elif reason in {"no_text", "no_mapper_adapter", "memory_mapping_not_selected"}:
        status = "not_attempted"
    else:
        status = "succeeded"

    return {
        "status": status,
        "changed": changed,
        "written_ids": written_ids,
        "forgotten_ids": forgotten_ids,
        "active_count": active_count,
        "candidate_count": candidate_count,
        "reason": reason,
    }


def task_not_wired_receipt() -> dict[str, Any]:
    return {
        "status": "not_attempted",
        "changed": False,
        "entity_ids": [],
        "reason": _NOT_WIRED_TASK_REASON,
    }


def reminder_not_wired_receipt() -> dict[str, Any]:
    return {
        "status": "not_attempted",
        "changed": False,
        "entity_ids": [],
        "operation": None,
        "reason": _NOT_WIRED_REMINDER_REASON,
    }


def group_reminder_receipt(action_state: dict[str, Any] | None) -> dict[str, Any]:
    if not action_state:
        return {
            "status": "not_attempted",
            "changed": False,
            "entity_ids": [],
            "operation": None,
            "reason": "no_group_reminder_action",
        }

    raw_status = str(action_state.get("status") or "")
    reminder_id = action_state.get("reminder_id")
    cancelled_count = int(action_state.get("cancelled_count") or 0)
    entity_ids = [str(reminder_id)] if reminder_id is not None else []
    reason = action_state.get("reason")

    if raw_status == "scheduled":
        if reminder_id is None:
            return {
                "status": "partial",
                "changed": False,
                "entity_ids": [],
                "operation": "create",
                "reason": "scheduled_without_persisted_id",
            }
        return {
            "status": "succeeded",
            "changed": True,
            "entity_ids": entity_ids,
            "operation": "create",
            "due_at": action_state.get("due_at"),
            "recurring": bool(action_state.get("recurring")),
            "interval_seconds": action_state.get("interval_seconds"),
            "target_username": action_state.get("target_username"),
            "stop_on_reply": bool(action_state.get("stop_on_reply")),
            "reason": reason,
        }

    if raw_status in {"cancelled", "cancelled_on_response"}:
        changed = cancelled_count > 0
        return {
            "status": "succeeded",
            "changed": changed,
            "entity_ids": entity_ids,
            "operation": "cancel",
            "cancelled_count": cancelled_count,
            "target_username": action_state.get("target_username"),
            "reason": reason if changed else "no_active_reminders",
        }

    if raw_status == "not_cancelled":
        return {
            "status": "succeeded",
            "changed": False,
            "entity_ids": [],
            "operation": "cancel",
            "cancelled_count": 0,
            "target_username": action_state.get("target_username"),
            "reason": reason or "no_active_reminders",
        }

    if raw_status == "not_scheduled":
        return {
            "status": "needs_clarification",
            "changed": False,
            "entity_ids": [],
            "operation": "create",
            "reason": reason or "reminder_not_scheduled",
        }

    if raw_status == "error":
        return {
            "status": "failed",
            "changed": False,
            "entity_ids": [],
            "operation": None,
            "reason": reason or "group_reminder_error",
        }

    return {
        "status": "partial",
        "changed": False,
        "entity_ids": entity_ids,
        "operation": None,
        "reason": f"unrecognized_group_reminder_status:{raw_status or 'empty'}",
    }


def personal_operation_receipts(mapper_result: Any | None) -> dict[str, Any]:
    return {
        "memory": memory_receipt(mapper_result, attempted=True),
        "task": task_not_wired_receipt(),
        "reminder": reminder_not_wired_receipt(),
    }


def group_operation_receipts(
    mapper_result: Any | None,
    *,
    memory_attempted: bool,
    reminder_action_state: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "memory": memory_receipt(mapper_result, attempted=memory_attempted),
        "task": task_not_wired_receipt(),
        "reminder": group_reminder_receipt(reminder_action_state),
    }
