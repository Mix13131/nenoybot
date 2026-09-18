from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.intervention_repo import InterventionRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore


NOW = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)


def _psycopg_and_url():
    database_url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    return psycopg, database_url


def _event(scope_id: str, text: str, *, event_type: EventType):
    return EventEnvelope(
        event_id=f"edit:{scope_id}:{event_type.value}:{text[:5]}",
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="7001",
        message_id="9001",
        text=text,
    )


def _candidate(text: str, semantic_key: str):
    return {
        "memory_type": "observation",
        "semantic_key": semantic_key,
        "summary": text,
        "subject_keys": ["user:7001"],
        "source_message_id": "9001",
        "evidence_excerpt": text,
        "payload": {
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": text,
            "claim_kind": "fact",
        },
        "importance": 0.7,
        "confidence": 0.7,
        "usage_policy": {
            "assist": True,
            "callback": True,
            "roast": False,
            "proactive": False,
        },
    }


def test_edited_source_can_change_semantic_key_without_duplicate_card() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-key-{uuid.uuid4().hex}"

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
        original = _event(scope_id, "Встреча во вторник", event_type=EventType.PRIVATE_MESSAGE)
        created = mapper._upsert_candidate(
            original,
            _candidate(original.text, "explicit:old-key"),
            explicit=True,
            recent_context=(),
        )
        assert created is not None

        edited_event = _event(scope_id, "Встреча отменена", event_type=EventType.EDITED_MESSAGE)
        corrected = mapper._upsert_candidate(
            edited_event,
            _candidate(edited_event.text, "explicit:new-key"),
            explicit=True,
            recent_context=(),
        )
        assert corrected is not None
        assert corrected.id == created.id
        assert corrected.source_count == 1
        assert corrected.payload["semantic_key"] == "explicit:new-key"
        assert len(corrected.evidence) == 1
        assert corrected.evidence[0].excerpt == "Встреча отменена"

        rows = conn.execute(
            """
            SELECT id, payload ->> 'semantic_key', source_count, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == created.id
        assert rows[0][1] == "explicit:new-key"
        assert rows[0][2] == 1
        assert len(rows[0][3]) == 1

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()


def test_group_forget_targets_resolve_from_sent_bot_intervention_in_same_scope() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"-{int(uuid.uuid4().int % 8_000_000_000 + 1_000_000_000)}"
    other_scope = f"-{int(uuid.uuid4().int % 8_000_000_000 + 10_000_000_000)}"
    telegram_message_id = int(uuid.uuid4().int % 1_000_000_000 + 1_000_000)

    with psycopg.connect(database_url) as conn:
        intervention_id = conn.execute(
            """
            INSERT INTO interventions(
                scope_type, scope_id, primary_action, mode,
                reason_codes, policy_version, selected_memory_ids,
                generated_text, created_at
            ) VALUES (
                'group', %s, 'reply', 'group_callback',
                '[]'::jsonb, 'test', %s::jsonb, 'synthetic reply', %s
            ) RETURNING id
            """,
            (scope_id, json.dumps(["mem-a", "mem-b"]), NOW),
        ).fetchone()[0]
        outbox_id = conn.execute(
            """
            INSERT INTO outbox(
                dedupe_key, channel, destination_id, payload,
                status, telegram_message_id, sent_at, created_at
            ) VALUES (%s, 'telegram', %s, %s::jsonb, 'sent', %s, %s, %s)
            RETURNING id
            """,
            (
                f"it-forget:{uuid.uuid4().hex}",
                scope_id,
                json.dumps({"metadata": {"intervention_id": str(intervention_id)}}),
                telegram_message_id,
                NOW,
                NOW,
            ),
        ).fetchone()[0]
        conn.commit()

        repo = InterventionRepository(conn)
        assert repo.selected_memory_ids_for_bot_message(
            scope_type=ScopeType.GROUP,
            scope_id=scope_id,
            telegram_message_id=str(telegram_message_id),
        ) == ("mem-a", "mem-b")
        assert repo.selected_memory_ids_for_bot_message(
            scope_type=ScopeType.GROUP,
            scope_id=other_scope,
            telegram_message_id=str(telegram_message_id),
        ) == ()

        conn.execute("DELETE FROM outbox WHERE id=%s", (outbox_id,))
        conn.execute("DELETE FROM interventions WHERE id=%s", (intervention_id,))
        conn.commit()
