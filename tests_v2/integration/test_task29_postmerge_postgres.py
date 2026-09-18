from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.repositories.message_repo import MessageRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore


NOW = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)


def _psycopg_and_url():
    database_url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    return psycopg, database_url


class _Adapter:
    def __init__(self, responses):
        self.responses = list(responses)

    def generate_json(self, *args, **kwargs):
        return SimpleNamespace(parsed=self.responses.pop(0))


def _candidate(*, semantic_key, excerpt, memory_type):
    return {
        "memory_type": memory_type,
        "semantic_key": semantic_key,
        "summary": "Отчёт будет отправлен завтра.",
        "subject_keys": ["user:991000001"],
        "source_message_id": "880300001",
        "evidence_excerpt": excerpt,
        "payload": {
            "statement_kind": "commitment" if memory_type == "commitment" else "none",
            "status": "open" if memory_type == "commitment" else "unknown",
            "due_at": None,
            "verbatim": excerpt,
            "claim_kind": "plan",
        },
        "importance": 0.8,
        "confidence": 0.9 if memory_type == "commitment" else 0.4,
        "usage_policy": {
            "assist": True,
            "callback": True,
            "roast": False,
            "proactive": True,
        },
    }


def test_retry_excerpt_drift_does_not_duplicate_card_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-retry-excerpt-{uuid.uuid4().hex}"
    event = EventEnvelope(
        event_id=f"{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880300001",
        text="Завтра отправлю отчёт",
    )
    mapper = None
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(
            store=MemoryMapperStore(MemoryRepository(conn)),
            adapter=_Adapter([
                {"candidates": [_candidate(
                    semantic_key="commitment:send-report",
                    excerpt="Завтра отправлю отчёт",
                    memory_type="commitment",
                )]},
                {"candidates": [_candidate(
                    semantic_key="observation:drifted",
                    excerpt="отправлю отчёт",
                    memory_type="observation",
                )]},
            ]),
        )
        first = mapper.map_event(event)
        assert len(first.written) == 1
        first_id = first.written[0].id

        retried = mapper.map_event(event)
        assert retried.written == ()

        rows = conn.execute(
            """
            SELECT id, payload ->> 'source_candidate_index', evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND status IN ('candidate','active')
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == first_id
        assert rows[0][1] == "0"
        assert len(rows[0][2]) == 1

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()


def _event_payload(*, event_id, scope_id, message_id, text, thread_id):
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": EventType.GROUP_MESSAGE.value,
            "occurred_at": NOW.isoformat(),
            "scope_type": ScopeType.GROUP.value,
            "scope_id": scope_id,
            "actor_user_id": "991000001",
            "message_id": message_id,
            "reply_to_message_id": None,
            "text": text,
            "metadata": {"message_thread_id": thread_id},
        },
        ensure_ascii=False,
    )


def test_unthreaded_replay_context_excludes_forum_topic_history_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    suffix = uuid.uuid4().hex[:10]
    scope_id = f"-100{int(suffix[:7], 16)}"
    unthreaded_event = f"it:{suffix}:plain"
    threaded_event = f"it:{suffix}:topic"
    boundary_event = f"it:{suffix}:boundary"

    with psycopg.connect(database_url) as conn:
        rows = [
            (unthreaded_event, 920000001, "880400001", "обычный контекст", None),
            (threaded_event, 920000002, "880400002", "контекст из темы", 77),
            (boundary_event, 920000003, "880400003", "текущая реплика", None),
        ]
        for event_id, update_id, message_id, text, thread_id in rows:
            conn.execute(
                """
                INSERT INTO events(
                    event_id, telegram_update_id, event_type,
                    scope_type, scope_id, payload
                ) VALUES (%s,%s,'group_message','group',%s,%s::jsonb)
                """,
                (
                    event_id,
                    update_id,
                    scope_id,
                    _event_payload(
                        event_id=event_id,
                        scope_id=scope_id,
                        message_id=message_id,
                        text=text,
                        thread_id=thread_id,
                    ),
                ),
            )
        conn.commit()

        context = MessageRepository(conn).recent_before_event(
            ScopeType.GROUP,
            scope_id,
            before=NOW,
            before_message_id="880400003",
            boundary_event_id=boundary_event,
            limit=12,
        )

        assert [(item.message_id, item.text) for item in context] == [
            ("880400001", "обычный контекст")
        ]

        conn.execute(
            "DELETE FROM events WHERE event_id IN (%s,%s,%s)",
            (unthreaded_event, threaded_event, boundary_event),
        )
        conn.commit()
