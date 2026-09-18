from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.repositories.message_repo import MessageRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore


NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)


def _psycopg_and_url():
    database_url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    return psycopg, database_url


def _payload(
    *,
    event_id: str,
    event_type: EventType,
    scope_id: str,
    message_id: str,
    text: str,
    at: datetime,
) -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type.value,
            "occurred_at": at.isoformat(),
            "scope_type": ScopeType.GROUP.value,
            "scope_id": scope_id,
            "actor_user_id": "991000001",
            "message_id": message_id,
            "reply_to_message_id": None,
            "text": text,
            "metadata": {},
        },
        ensure_ascii=False,
    )


def test_replay_context_uses_pre_boundary_event_version_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    suffix = uuid.uuid4().hex[:10]
    scope_id = f"-100{int(suffix[:7], 16)}"
    other_scope_id = f"-200{int(suffix[:7], 16)}"
    v1_event_id = f"it:{suffix}:m1:v1"
    boundary_event_id = f"it:{suffix}:e2"
    v2_event_id = f"it:{suffix}:m1:v2"
    other_event_id = f"it:{suffix}:other"
    message_id = "880100001"
    boundary_message_id = "880100002"

    with psycopg.connect(database_url) as conn:
        rows = [
            (v1_event_id, 910000001, EventType.GROUP_MESSAGE, scope_id, message_id, "версия один", NOW),
            (boundary_event_id, 910000002, EventType.GROUP_MESSAGE, scope_id, boundary_message_id, "граница", NOW),
            # Same timestamp but later Telegram update: must not leak into replay of E2.
            (v2_event_id, 910000003, EventType.EDITED_MESSAGE, scope_id, message_id, "версия два", NOW),
            (other_event_id, 910000001 + 50, EventType.GROUP_MESSAGE, other_scope_id, "880100099", "чужая группа", NOW),
        ]
        for event_id, update_id, event_type, event_scope, msg_id, text, at in rows:
            conn.execute(
                """
                INSERT INTO events(
                    event_id, telegram_update_id, event_type, scope_type, scope_id, payload
                ) VALUES (%s,%s,%s,'group',%s,%s::jsonb)
                """,
                (
                    event_id,
                    update_id,
                    event_type.value,
                    event_scope,
                    _payload(
                        event_id=event_id,
                        event_type=event_type,
                        scope_id=event_scope,
                        message_id=msg_id,
                        text=text,
                        at=at,
                    ),
                ),
            )

        # Mutable projection intentionally contains the future edit.
        user_id = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (%s) RETURNING id",
            (991000001 + int(suffix[:4], 16),),
        ).fetchone()[0]
        chat_id = conn.execute(
            "INSERT INTO chats(telegram_chat_id, chat_type) VALUES (%s,'group') RETURNING id",
            (int(scope_id),),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO messages(chat_id, user_id, telegram_message_id, text, created_at)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (chat_id, user_id, int(message_id), "версия два", NOW),
        )
        conn.commit()

        context = MessageRepository(conn).recent_before_event(
            ScopeType.GROUP,
            scope_id,
            before=NOW,
            before_message_id=boundary_message_id,
            boundary_event_id=boundary_event_id,
            limit=12,
        )

        assert [(item.message_id, item.text) for item in context] == [(message_id, "версия один")]
        assert all(item.text != "версия два" for item in context)
        assert all(item.text != "чужая группа" for item in context)

        conn.execute("DELETE FROM events WHERE event_id IN (%s,%s,%s,%s)", (v1_event_id, boundary_event_id, v2_event_id, other_event_id))
        conn.execute("DELETE FROM chats WHERE id=%s", (chat_id,))
        conn.execute("DELETE FROM users WHERE id=%s", (user_id,))
        conn.commit()


class _EditAdapter:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def generate_json(self, *args, **kwargs):
        return SimpleNamespace(parsed=self.responses.pop(0))


def _candidate(excerpt: str) -> dict:
    return {
        "memory_type": "commitment",
        "semantic_key": "alpha:planned",
        "summary": "Альфа запланирована.",
        "subject_keys": ["user:991000001"],
        "source_message_id": "880200001",
        "evidence_excerpt": excerpt,
        "payload": {
            "statement_kind": "commitment",
            "status": "open",
            "due_at": None,
            "verbatim": excerpt,
            "claim_kind": "plan",
        },
        "importance": 0.8,
        "confidence": 0.9,
        "usage_policy": {"assist": True, "callback": True, "roast": False, "proactive": True},
    }


def test_group_low_signal_edit_reconciliation_is_idempotent_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-group-edit-{uuid.uuid4().hex}"
    original = EventEnvelope(
        event_id=f"{scope_id}:original",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880200001",
        text="Альфа отправится 20-го.",
    )
    edited = original.model_copy(
        update={
            "event_id": f"{scope_id}:edit",
            "event_type": EventType.EDITED_MESSAGE,
            "occurred_at": NOW + timedelta(minutes=5),
            "text": "ок",
        }
    )
    adapter = _EditAdapter(
        [
            {"candidates": [_candidate("Альфа отправится 20-го")]},
            {"candidates": []},
            {"candidates": []},
        ]
    )

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(original)
        assert first.failed is False
        assert len(first.written) == 1
        memory_id = first.written[0].id

        cleared = mapper.map_event(edited)
        assert cleared.failed is False
        assert cleared.forgotten_ids == (memory_id,)
        row = conn.execute(
            "SELECT status FROM memory_cards WHERE id=%s",
            (memory_id,),
        ).fetchone()
        assert row == ("archived",)

        retried = mapper.map_event(edited)
        assert retried.failed is False
        assert retried.forgotten_ids == ()
        assert retried.written == ()

        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='group' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()
