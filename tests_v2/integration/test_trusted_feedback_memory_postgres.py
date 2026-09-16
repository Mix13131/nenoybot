from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _database_url() -> str | None:
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


def _psycopg_and_url():
    database_url = _database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    return psycopg, database_url


def _explicit_event(scope_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"it:{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880000001",
        text="Запомни: конкурентный тест памяти",
    )


def test_same_memory_source_concurrent_postgres_retry_creates_one_card() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-memory-{uuid.uuid4().hex}"
    event = _explicit_event(scope_id)
    barrier = threading.Barrier(2)
    results = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            with psycopg.connect(database_url) as conn:
                mapper = MemoryMapper(
                    store=MemoryMapperStore(MemoryRepository(conn)),
                )
                barrier.wait(timeout=5)
                result = mapper.map_event(event)
                results.append(result)
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert all(result.failed is False for result in results)
    assert all(len(result.written) == 1 for result in results)
    assert results[0].written[0].id == results[1].written[0].id

    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            """
            SELECT id, confidence, source_count, evidence, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == pytest.approx(0.98)
        assert rows[0][2] == 1
        assert len(rows[0][3]) == 1
        original_updated_at = rows[0][4]

    # A later retry through a fresh DB connection must be a persistence no-op.
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
        retried = mapper.map_event(event)
        assert retried.failed is False
        assert len(retried.written) == 1
        assert retried.written[0].source_count == 1

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            """
            SELECT confidence, source_count, jsonb_array_length(evidence), updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            """,
            (scope_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(0.98)
        assert row[1] == 1
        assert row[2] == 1
        assert row[3] == original_updated_at
        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()


def test_current_reaction_vote_postgres_uses_latest_state_and_keeps_text_feedback() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-feedback-{uuid.uuid4().hex}"
    since = NOW - timedelta(minutes=5)

    with psycopg.connect(database_url) as conn:
        user1 = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (%s) RETURNING id",
            (992000001,),
        ).fetchone()[0]
        user2 = conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (%s) RETURNING id",
            (992000002,),
        ).fetchone()[0]
        intervention_id = conn.execute(
            """
            INSERT INTO interventions(
                scope_type, scope_id, primary_action, mode, reason_codes, policy_version
            ) VALUES ('group', %s, 'reply', 'group_roast', '[]'::jsonb, 'test')
            RETURNING id
            """,
            (scope_id,),
        ).fetchone()[0]

        def insert_feedback(feedback_id: str, user_id: int, feedback_type: str, at: datetime) -> None:
            conn.execute(
                """
                INSERT INTO feedback_events(
                    intervention_id, scope_id, user_id, feedback_type,
                    value, payload, created_at, feedback_id
                ) VALUES (%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s)
                """,
                (
                    intervention_id,
                    scope_id,
                    user_id,
                    feedback_type,
                    1.0 if feedback_type == 'reaction_positive' else -1.0 if feedback_type in {'reaction_negative','explicit_negative'} else 0.0,
                    at,
                    feedback_id,
                ),
            )

        # user1 changes + to -: only the latest negative reaction may count.
        insert_feedback("it:u1:plus", user1, "reaction_positive", NOW)
        insert_feedback("it:u1:minus", user1, "reaction_negative", NOW + timedelta(seconds=1))
        # user2 removes a +: neither positive nor negative remains current.
        insert_feedback("it:u2:plus", user2, "reaction_positive", NOW)
        insert_feedback("it:u2:removed", user2, "reaction_removed", NOW + timedelta(seconds=2))
        # Explicit text feedback remains an independent event signal.
        insert_feedback("it:u2:text-negative", user2, "explicit_negative", NOW + timedelta(seconds=3))
        conn.commit()

        repo = GroupInitiativeRepository(conn)
        assert repo.count_feedback_since(scope_id, ["reaction_positive"], since) == 0
        assert repo.count_feedback_since(scope_id, ["reaction_negative"], since) == 1
        assert repo.count_feedback_since(scope_id, ["explicit_negative"], since) == 1
        assert repo.count_feedback_since(
            scope_id,
            ["reaction_negative", "explicit_negative"],
            since,
        ) == 2

        conn.execute("DELETE FROM feedback_events WHERE scope_id=%s", (scope_id,))
        conn.execute("DELETE FROM interventions WHERE id=%s", (intervention_id,))
        conn.execute("DELETE FROM users WHERE id IN (%s,%s)", (user1, user2))
        conn.commit()
