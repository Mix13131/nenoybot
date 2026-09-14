from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.enums import ScopeType


@dataclass(frozen=True)
class TaskRecord:
    id: int
    scope_type: ScopeType
    scope_id: str
    title: str
    status: str
    due_at: datetime | None
    payload: dict[str, Any]


class TaskRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def create(self, *, scope_type: ScopeType, scope_id: str, title: str, due_at: datetime | None = None, payload: dict[str, Any] | None = None) -> TaskRecord:
        row = self.conn.execute(
            """
            INSERT INTO tasks(scope_type, scope_id, title, status, due_at, payload)
            VALUES (%s,%s,%s,'open',%s,%s::jsonb)
            RETURNING id, scope_type, scope_id, title, status, due_at, payload
            """,
            (scope_type.value, scope_id, title, due_at, json.dumps(payload or {}, ensure_ascii=False)),
        ).fetchone()
        self.conn.commit()
        return self._row(row)

    def update(self, task_id: int, *, title: str | None = None, due_at: datetime | None = None, payload: dict[str, Any] | None = None) -> TaskRecord | None:
        row = self.conn.execute(
            """
            UPDATE tasks
            SET title=COALESCE(%s, title),
                due_at=COALESCE(%s, due_at),
                payload=CASE WHEN %s IS NULL THEN payload ELSE %s::jsonb END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND status='open'
            RETURNING id, scope_type, scope_id, title, status, due_at, payload
            """,
            (title, due_at, json.dumps(payload, ensure_ascii=False) if payload is not None else None, json.dumps(payload or {}, ensure_ascii=False), task_id),
        ).fetchone()
        self.conn.commit()
        return self._row(row) if row else None

    def complete(self, task_id: int) -> bool:
        row = self.conn.execute(
            "UPDATE tasks SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE id=%s AND status='open' RETURNING id",
            (task_id,),
        ).fetchone()
        self.conn.commit()
        return row is not None

    @staticmethod
    def _row(row) -> TaskRecord:
        return TaskRecord(int(row[0]), ScopeType(row[1]), str(row[2]), str(row[3]), str(row[4]), row[5], dict(row[6] or {}))
