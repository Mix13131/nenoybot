from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.actions import ActionRequest
from app_v2.domain.enums import ScopeType


class ActionEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionResult:
    action_type: str
    entity_id: int | None
    changed: bool
    payload: dict[str, Any]


class ActionEngine:
    def __init__(self, *, task_repo: Any, reminder_repo: Any) -> None:
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo

    def execute(self, request: ActionRequest) -> ActionResult:
        action = request.action_type.strip().lower()
        payload = dict(request.payload)

        if action == "create_task":
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ActionEngineError("create_task requires payload.title")
            due_at = self._optional_datetime(payload.get("due_at"))
            record = self.task_repo.create(
                scope_type=request.scope_type,
                scope_id=request.scope_id,
                title=title,
                due_at=due_at,
                payload={**payload, "actor_user_id": request.actor_user_id},
            )
            return ActionResult(action, record.id, True, {"status": record.status, "title": record.title})

        if action == "update_task":
            task_id = self._required_int(payload, "task_id")
            record = self.task_repo.update(
                task_id,
                title=payload.get("title"),
                due_at=self._optional_datetime(payload.get("due_at")),
                payload=payload.get("data"),
            )
            return ActionResult(action, task_id, record is not None, {"status": record.status if record else "not_found"})

        if action == "complete_task":
            task_id = self._required_int(payload, "task_id")
            changed = self.task_repo.complete(task_id)
            return ActionResult(action, task_id, changed, {"status": "completed" if changed else "not_found"})

        if action == "create_reminder":
            due_at = self._required_datetime(payload, "due_at")
            text = str(payload.get("text") or payload.get("title") or "Напоминание").strip()
            record = self.reminder_repo.create(
                scope_type=request.scope_type,
                scope_id=request.scope_id,
                due_at=due_at,
                recurrence_rule=payload.get("recurrence_rule"),
                payload={
                    **payload,
                    "text": text,
                    "actor_user_id": request.target_user_id or request.actor_user_id,
                },
            )
            return ActionResult(action, record.id, True, {"status": record.status, "due_at": record.due_at.isoformat()})

        if action == "cancel_reminder":
            reminder_id = self._required_int(payload, "reminder_id")
            changed = self.reminder_repo.cancel(reminder_id)
            return ActionResult(action, reminder_id, changed, {"status": "cancelled" if changed else "not_found"})

        if action == "reschedule_reminder":
            reminder_id = self._required_int(payload, "reminder_id")
            due_at = self._required_datetime(payload, "due_at")
            record = self.reminder_repo.reschedule(reminder_id, due_at)
            return ActionResult(action, reminder_id, record is not None, {"status": record.status if record else "not_found", "due_at": due_at.isoformat()})

        raise ActionEngineError(f"Unsupported action_type: {request.action_type}")

    @staticmethod
    def _required_int(payload: dict[str, Any], key: str) -> int:
        try:
            return int(payload[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionEngineError(f"{key} must be an integer") from exc

    @classmethod
    def _required_datetime(cls, payload: dict[str, Any], key: str) -> datetime:
        if key not in payload:
            raise ActionEngineError(f"{key} is required")
        value = cls._optional_datetime(payload[key])
        if value is None:
            raise ActionEngineError(f"{key} is required")
        return value

    @staticmethod
    def _optional_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ActionEngineError("datetime must be an ISO-8601 string or datetime")
        if result.tzinfo is None or result.utcoffset() is None:
            raise ActionEngineError("datetime must be timezone-aware")
        return result
