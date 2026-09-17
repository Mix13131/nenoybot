from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.analytics_repo import AnalyticsRepository
from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore


NOW = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)


def _psycopg_and_url():
    database_url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    return psycopg, database_url


def _event(scope_id: str, message_id: str, actor: str, text: str, offset_minutes: int = 0):
    return EventEnvelope(
        event_id=f"it:{scope_id}:{message_id}",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW + timedelta(minutes=offset_minutes),
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id=actor,
        message_id=message_id,
        text=text,
    )


def _candidate(event: EventEnvelope, *, semantic_key="observation:shared"):
    return {
        "memory_type": "observation",
        "semantic_key": semantic_key,
        "summary": event.text,
        "subject_keys": [f"user:{event.actor_user_id}"],
        "source_message_id": event.message_id,
        "evidence_excerpt": event.text,
        "payload": {
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": event.text,
            "claim_kind": "fact",
        },
        "importance": 0.6,
        "confidence": 0.6,
        "usage_policy": {
            "assist": True,
            "callback": True,
            "roast": False,
            "proactive": False,
        },
    }


def _run_upsert(database_url, psycopg, event, barrier, results, errors):
    try:
        with psycopg.connect(database_url) as conn:
            mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
            barrier.wait(timeout=5)
            card = mapper._upsert_candidate(
                event,
                _candidate(event),
                explicit=False,
                recent_context=(),
            )
            results.append(card)
    except Exception as exc:  # pragma: no cover - surfaced below
        errors.append(exc)


def test_distinct_sources_concurrent_create_one_semantic_card() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-semantic-create-{uuid.uuid4().hex}"
    events = [
        _event(scope_id, "101", "1001", "Проект стартует в октябре"),
        _event(scope_id, "102", "1002", "Старт проекта подтверждён на октябрь", 1),
    ]
    barrier = threading.Barrier(2)
    results = []
    errors = []
    threads = [
        threading.Thread(
            target=_run_upsert,
            args=(database_url, psycopg, event, barrier, results, errors),
        )
        for event in events
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert all(card is not None for card in results)

    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, source_count, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND payload ->> 'semantic_key'='observation:shared'
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == 2
        assert {str(item["message_id"]) for item in rows[0][2]} == {"101", "102"}
        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()


def test_distinct_sources_concurrent_merge_existing_card_keeps_all_evidence() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-semantic-merge-{uuid.uuid4().hex}"
    initial = _event(scope_id, "200", "2000", "Проект стартует в октябре")

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
        created = mapper._upsert_candidate(
            initial,
            _candidate(initial),
            explicit=False,
            recent_context=(),
        )
        assert created is not None

    events = [
        _event(scope_id, "201", "2001", "Октябрьский старт подтверждает команда", 1),
        _event(scope_id, "202", "2002", "Партнёр тоже подтвердил старт в октябре", 2),
    ]
    barrier = threading.Barrier(2)
    results = []
    errors = []
    threads = [
        threading.Thread(
            target=_run_upsert,
            args=(database_url, psycopg, event, barrier, results, errors),
        )
        for event in events
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert all(card is not None for card in results)

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            """
            SELECT source_count, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND payload ->> 'semantic_key'='observation:shared'
            """,
            (scope_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == 3
        assert {str(item["message_id"]) for item in row[1]} == {"200", "201", "202"}
        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()


def test_reaction_current_state_prefers_telegram_update_sequence_over_insert_order() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-reaction-seq-{uuid.uuid4().hex}"
    since = NOW - timedelta(minutes=5)
    until = NOW + timedelta(minutes=5)

    with psycopg.connect(database_url) as conn:
        user1 = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (%s) RETURNING id",
            (993100001,),
        ).fetchone()[0]
        user2 = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (%s) RETURNING id",
            (993100002,),
        ).fetchone()[0]
        intervention1 = conn.execute(
            """
            INSERT INTO interventions(
                scope_type, scope_id, primary_action, mode,
                reason_codes, policy_version, created_at
            ) VALUES ('group', %s, 'reply', 'group_roast', '[]'::jsonb, 'test', %s)
            RETURNING id
            """,
            (scope_id, NOW),
        ).fetchone()[0]
        intervention2 = conn.execute(
            """
            INSERT INTO interventions(
                scope_type, scope_id, primary_action, mode,
                reason_codes, policy_version, created_at
            ) VALUES ('group', %s, 'reply', 'group_roast', '[]'::jsonb, 'test', %s)
            RETURNING id
            """,
            (scope_id, NOW),
        ).fetchone()[0]

        def insert_reaction(feedback_id, intervention_id, user_id, feedback_type, payload):
            conn.execute(
                """
                INSERT INTO feedback_events(
                    feedback_id, intervention_id, scope_id, user_id,
                    feedback_type, value, payload, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    feedback_id,
                    intervention_id,
                    scope_id,
                    user_id,
                    feedback_type,
                    1.0 if feedback_type == "reaction_positive" else -1.0 if feedback_type == "reaction_negative" else 0.0,
                    json.dumps(payload),
                    NOW,
                ),
            )

        # Newer Telegram update is inserted first. The older update arrives at
        # the DB later, with the same Telegram second, and must NOT resurrect +.
        insert_reaction(
            "it:seq:new-remove",
            intervention1,
            user1,
            "reaction_removed",
            {"source_event_id": "tg:200"},
        )
        insert_reaction(
            "it:seq:old-positive",
            intervention1,
            user1,
            "reaction_positive",
            {"source_event_id": "tg:199", "reaction_families": ["approval_support"]},
        )

        # Non-Telegram/synthetic rows retain created_at/id fallback. With tied
        # timestamps, the later inserted row is the current one.
        insert_reaction(
            "it:fallback:positive",
            intervention2,
            user2,
            "reaction_positive",
            {"reaction_families": ["approval_support"]},
        )
        insert_reaction(
            "it:fallback:negative",
            intervention2,
            user2,
            "reaction_negative",
            {"reaction_families": ["explicit_negative"]},
        )
        conn.commit()

        initiative = GroupInitiativeRepository(conn)
        assert initiative.count_feedback_since(scope_id, ["reaction_positive"], since) == 0
        assert initiative.count_feedback_since(scope_id, ["reaction_negative"], since) == 1

        by_mode = AnalyticsRepository(conn).reaction_quality_by_mode(since, until, scope_id)
        assert len(by_mode) == 1
        assert by_mode[0]["reacting_states"] == 1
        assert by_mode[0]["positive_votes"] == 0
        assert by_mode[0]["negative_votes"] == 1

        conn.execute("DELETE FROM feedback_events WHERE scope_id=%s", (scope_id,))
        conn.execute("DELETE FROM interventions WHERE id IN (%s,%s)", (intervention1, intervention2))
        conn.execute("DELETE FROM users WHERE id IN (%s,%s)", (user1, user2))
        conn.commit()
