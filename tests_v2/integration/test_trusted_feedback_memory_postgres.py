from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.analytics_repo import AnalyticsRepository
from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore
from app_v2.services.operation_receipts import personal_operation_receipts


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


class _EditAdapter:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def generate_json(self, *args, **kwargs):
        return SimpleNamespace(parsed=self.responses.pop(0))


def _edit_candidate(key: str, summary: str, excerpt: str) -> dict:
    return {
        "memory_type": "commitment",
        "semantic_key": key,
        "summary": summary,
        "subject_keys": ["user:991000001"],
        "source_message_id": "880009999",
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


def test_multi_card_edited_source_reconciles_full_batch_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-batch-{uuid.uuid4().hex}"
    original_text = "Альфа отправится 20-го. Бета стоит 900 USD."
    edited_text = "Альфа отменена. Бета стоит 750 USD."
    adapter = _EditAdapter([
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
        {"candidates": [
            _edit_candidate("alpha:cancelled", "Альфа отменена.", "Альфа отменена"),
            _edit_candidate("beta:750", "Бета стоит 750 USD.", "Бета стоит 750 USD"),
        ]},
    ])
    base_event = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text=original_text,
    )
    edit_event = base_event.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": edited_text,
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base_event)
        corrected = mapper.map_event(edit_event)
        assert len(first.written) == 2
        assert corrected.failed is False
        assert len(corrected.written) == 2
        rows = conn.execute(
            """
            SELECT status, source_count, payload ->> 'semantic_key', evidence
            FROM memory_cards WHERE scope_type='personal' AND scope_id=%s
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        assert {row[0] for row in rows} <= {"candidate", "active"}
        assert {row[1] for row in rows} == {1}
        assert {row[2] for row in rows} == {"alpha:cancelled", "beta:750"}
        assert {row[3][0]["excerpt"] for row in rows} == {"Альфа отменена", "Бета стоит 750 USD"}
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


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
    assert sorted(len(result.written) for result in results) == [0, 1]
    changed_result = next(result for result in results if result.written)
    noop_result = next(result for result in results if not result.written)
    assert personal_operation_receipts(changed_result)["memory"]["changed"] is True
    assert personal_operation_receipts(noop_result)["memory"]["changed"] is False

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
        assert rows[0][0] == changed_result.written[0].id
        assert rows[0][1] == pytest.approx(0.98)
        assert rows[0][2] == 1
        assert len(rows[0][3]) == 1
        original_updated_at = rows[0][4]

    # A later retry through a fresh DB connection must be a persistence and
    # receipt no-op: the card exists, but this invocation did not write it.
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
        retried = mapper.map_event(event)
        assert retried.failed is False
        assert retried.written == ()
        retry_receipt = personal_operation_receipts(retried)["memory"]
        assert retry_receipt["status"] == "succeeded"
        assert retry_receipt["changed"] is False
        assert retry_receipt["written_ids"] == []

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
    until = NOW + timedelta(minutes=5)

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
                scope_type, scope_id, primary_action, mode,
                reason_codes, policy_version, created_at
            ) VALUES ('group', %s, 'reply', 'group_roast', '[]'::jsonb, 'test', %s)
            RETURNING id
            """,
            (scope_id, NOW),
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

        analytics = AnalyticsRepository(conn).reaction_quality_by_mode(since, until, scope_id)
        assert len(analytics) == 1
        assert analytics[0]["mode"] == "group_roast"
        assert analytics[0]["interventions"] == 1
        assert analytics[0]["reacting_states"] == 1
        assert analytics[0]["positive_votes"] == 0
        assert analytics[0]["negative_votes"] == 1
        assert analytics[0]["positive_share"] == 0.0

        conn.execute("DELETE FROM feedback_events WHERE scope_id=%s", (scope_id,))
        conn.execute("DELETE FROM interventions WHERE id=%s", (intervention_id,))
        conn.execute("DELETE FROM users WHERE id IN (%s,%s)", (user1, user2))
        conn.commit()


# issue92-final-archive-postgres-regressions

def test_edited_zero_candidates_archives_source_and_receipt_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-clear-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [_edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го")]},
        {"candidates": []},
        {"candidates": []},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "План отправки удалён из сообщения.",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        memory_id = first.written[0].id
        cleared = mapper.map_event(edited)
        assert cleared.failed is False
        assert cleared.forgotten_ids == (memory_id,)
        receipt = personal_operation_receipts(cleared)["memory"]
        assert receipt["status"] == "succeeded"
        assert receipt["changed"] is True
        assert receipt["forgotten_ids"] == [memory_id]
        row = conn.execute(
            "SELECT status, source_count FROM memory_cards WHERE id=%s",
            (memory_id,),
        ).fetchone()
        assert row == ("archived", 1)

        retried = mapper.map_event(edited)
        assert retried.forgotten_ids == ()
        assert personal_operation_receipts(retried)["memory"]["changed"] is False
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


def test_edited_source_archives_removed_sibling_and_reports_change_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-sibling-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
        ]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го. Бета стоит 900 USD.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Альфа отправится 20-го.",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
        beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
        result = mapper.map_event(edited)
        assert result.failed is False
        assert result.written == ()
        assert result.forgotten_ids == (beta.id,)
        receipt = personal_operation_receipts(result)["memory"]
        assert receipt["changed"] is True
        assert receipt["forgotten_ids"] == [beta.id]
        rows = dict(conn.execute(
            "SELECT id, status FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        ).fetchall())
        assert rows[alpha.id] in {"active", "candidate"}
        assert rows[beta.id] == "archived"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


# issue92-correction-safety-postgres-final

def test_shared_card_edit_splits_changed_source_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-shared-split-{uuid.uuid4().hex}"

    def c(key, summary, source, excerpt, memory_type="commitment", confidence=0.9):
        value = _edit_candidate(key, summary, excerpt)
        value["source_message_id"] = source
        value["memory_type"] = memory_type
        value["confidence"] = confidence
        return value

    adapter = _EditAdapter([
        {"candidates": [c("shipment:planned", "Отправка запланирована.", "880010001", "Отправка запланирована")]},
        {"candidates": [c("shipment:planned", "Отправка запланирована.", "880010002", "Да, отправка запланирована")]},
        {"candidates": [c("shipment:cancelled", "Отправка отменена.", "880010001", "Отправка отменена")]},
    ])
    first_event = EventEnvelope(
        event_id=f"it:{scope_id}:a",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880010001",
        text="Отправка запланирована",
    )
    second_event = first_event.model_copy(update={
        "event_id": f"it:{scope_id}:b",
        "message_id": "880010002",
        "occurred_at": NOW + timedelta(hours=7),
        "text": "Да, отправка запланирована",
    })
    edited = first_event.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(hours=8),
        "text": "Отправка отменена",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        old_id = mapper.map_event(first_event).written[0].id
        mapper.map_event(second_event)
        result = mapper.map_event(edited)
        assert result.failed is False
        rows = conn.execute(
            """
            SELECT id, status, source_count, payload ->> 'semantic_key', summary, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s AND status IN ('candidate','active')
            ORDER BY payload ->> 'semantic_key'
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        by_key = {row[3]: row for row in rows}
        planned = by_key["shipment:planned"]
        cancelled = by_key["shipment:cancelled"]
        assert planned[0] == old_id
        assert planned[2] == 1
        assert planned[4] == "Отправка запланирована."
        assert [item["message_id"] for item in planned[5]] == ["880010002"]
        assert cancelled[2] == 1
        assert [item["message_id"] for item in cancelled[5]] == ["880010001"]
        assert {card.id for card in result.written} == {planned[0], cancelled[0]}
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


def test_rejected_edit_candidate_does_not_erase_postgres_memory() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-invalid-edit-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [_edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го")]},
        {"candidates": [_edit_candidate("alpha:cancelled", "Альфа отменена.", "Фрагмента нет в edit")]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Альфа пока под вопросом.",
    })
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        memory_id = mapper.map_event(base).written[0].id
        result = mapper.map_event(edited)
        assert result.failed is True
        assert result.reason == "edited_source_invalid_candidate"
        row = conn.execute("SELECT status, evidence FROM memory_cards WHERE id=%s", (memory_id,)).fetchone()
        assert row[0] in {"candidate", "active"}
        assert row[1][0]["excerpt"] == "Альфа отправится 20-го"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


# issue92-pre-mutation-matching-postgres-final

def test_edit_retaining_later_candidate_uses_semantic_identity_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-retain-beta-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
        {"candidates": [
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го. Бета стоит 900 USD.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Бета стоит 900 USD.",
    })
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
        beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
        result = mapper.map_event(edited)
        assert result.failed is False
        assert result.forgotten_ids == (alpha.id,)
        rows = dict(conn.execute(
            "SELECT id, status FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        ).fetchall())
        assert rows[alpha.id] == "archived"
        assert rows[beta.id] in {"candidate", "active"}
        beta_row = conn.execute(
            "SELECT payload ->> 'semantic_key', summary, evidence FROM memory_cards WHERE id=%s",
            (beta.id,),
        ).fetchone()
        assert beta_row[0] == "beta:900"
        assert beta_row[1] == "Бета стоит 900 USD."
        assert beta_row[2][0]["excerpt"] == "Бета стоит 900 USD"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


def test_edited_source_merges_into_existing_semantic_card_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-merge-existing-{uuid.uuid4().hex}"

    def sourced_candidate(key: str, summary: str, excerpt: str, message_id: str) -> dict:
        value = _edit_candidate(key, summary, excerpt)
        value["source_message_id"] = message_id
        return value

    adapter = _EditAdapter([
        {"candidates": [sourced_candidate(
            "claim:a", "Синтетический проект использует вариант A.",
            "Проект использует вариант A", "880030001",
        )]},
        {"candidates": [sourced_candidate(
            "claim:b", "Синтетический проект использует вариант B.",
            "Проект использует вариант B", "880030002",
        )]},
        {"candidates": [sourced_candidate(
            "claim:b", "Синтетический проект использует вариант B.",
            "Теперь проект использует вариант B", "880030001",
        )]},
        {"candidates": [sourced_candidate(
            "claim:b", "Синтетический проект использует вариант B.",
            "Теперь проект использует вариант B", "880030001",
        )]},
    ])
    source_a = EventEnvelope(
        event_id=f"it:{scope_id}:a",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880030001",
        text="Проект использует вариант A",
    )
    source_b = source_a.model_copy(update={
        "event_id": f"it:{scope_id}:b",
        "occurred_at": NOW + timedelta(hours=7),
        "message_id": "880030002",
        "text": "Проект использует вариант B",
    })
    edited = source_a.model_copy(update={
        "event_id": f"it:{scope_id}:edit-a-to-b",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Теперь проект использует вариант B",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        old_a = mapper.map_event(source_a).written[0]
        existing_b = mapper.map_event(source_b).written[0]
        corrected = mapper.map_event(edited)

        assert corrected.failed is False
        assert corrected.forgotten_ids == (old_a.id,)
        assert [card.id for card in corrected.written] == [existing_b.id]
        rows = conn.execute(
            """
            SELECT id, status, source_count, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        by_id = {row[0]: row for row in rows}
        assert by_id[old_a.id][1] == "archived"
        assert by_id[existing_b.id][2] == 2
        assert {item["message_id"] for item in by_id[existing_b.id][3]} == {
            "880030001", "880030002",
        }
        receipt = personal_operation_receipts(corrected)["memory"]
        assert receipt["changed"] is True
        assert receipt["written_ids"] == [existing_b.id]
        assert receipt["forgotten_ids"] == [old_a.id]

        before_retry = by_id[existing_b.id]
        retried = mapper.map_event(edited)
        assert retried.written == ()
        assert retried.forgotten_ids == ()
        assert personal_operation_receipts(retried)["memory"]["changed"] is False
        after_retry = conn.execute(
            "SELECT id, status, source_count, evidence FROM memory_cards WHERE id=%s",
            (existing_b.id,),
        ).fetchone()
        assert after_retry == before_retry
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()



def test_same_source_retry_classification_drift_is_noop_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-classification-retry-{uuid.uuid4().hex}"
    message_id = "880040001"

    def retry_candidate(memory_type: str, semantic_key: str, confidence: float) -> dict:
        return {
            "memory_type": memory_type,
            "semantic_key": semantic_key,
            "summary": "Пользователь отправит отчёт завтра.",
            "subject_keys": ["user:991000001"],
            "source_message_id": message_id,
            "evidence_excerpt": "Завтра отправлю отчёт",
            "payload": {
                "statement_kind": "commitment" if memory_type == "commitment" else "none",
                "status": "open",
                "due_at": None,
                "verbatim": "Завтра отправлю отчёт",
                "claim_kind": "plan",
            },
            "importance": 0.8,
            "confidence": confidence,
            "usage_policy": {
                "assist": True,
                "callback": True,
                "roast": False,
                "proactive": True,
            },
        }

    adapter = _EditAdapter([
        {"candidates": [retry_candidate("commitment", "commitment:send-report", 0.9)]},
        {"candidates": [retry_candidate("observation", "observation:drifted-key", 0.4)]},
    ])
    source = EventEnvelope(
        event_id=f"it:{scope_id}:source",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id=message_id,
        text="Завтра отправлю отчёт",
    )

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(source)
        first_card = first.written[0]
        before = conn.execute(
            "SELECT id, memory_type, payload, evidence, source_count, updated_at FROM memory_cards WHERE id=%s",
            (first_card.id,),
        ).fetchone()

        retried = mapper.map_event(source)

        assert retried.written == ()
        rows = conn.execute(
            """
            SELECT id, memory_type, payload, evidence, source_count, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            """,
            (scope_id,),
        ).fetchall()
        assert rows == [before]
        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()


def test_group_person_specific_semantic_key_isolated_by_author_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-author-semantic-{uuid.uuid4().hex}"

    def person_candidate(author_id: str, message_id: str) -> dict:
        return {
            "memory_type": "commitment",
            "semantic_key": "commitment:send-report",
            "summary": "Участник отправит отчёт завтра.",
            "subject_keys": [f"user:{author_id}"],
            "source_message_id": message_id,
            "evidence_excerpt": "Завтра отправлю отчёт",
            "payload": {
                "statement_kind": "commitment",
                "status": "open",
                "due_at": None,
                "verbatim": "Завтра отправлю отчёт",
                "claim_kind": "plan",
            },
            "importance": 0.8,
            "confidence": 0.9,
            "usage_policy": {
                "assist": True,
                "callback": True,
                "roast": False,
                "proactive": True,
            },
        }

    adapter = _EditAdapter([
        {"candidates": [person_candidate("991000001", "880040101")]},
        {"candidates": [person_candidate("991000002", "880040102")]},
    ])
    first_event = EventEnvelope(
        event_id=f"it:{scope_id}:u1",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880040101",
        text="Завтра отправлю отчёт",
    )
    second_event = first_event.model_copy(update={
        "event_id": f"it:{scope_id}:u2",
        "occurred_at": NOW + timedelta(minutes=1),
        "actor_user_id": "991000002",
        "message_id": "880040102",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(first_event)
        second = mapper.map_event(second_event)

        assert len(first.written) == 1
        assert len(second.written) == 1
        assert first.written[0].id != second.written[0].id

        rows = conn.execute(
            """
            SELECT id, subject_keys, evidence, source_count
            FROM memory_cards
            WHERE scope_type='group'
              AND scope_id=%s
              AND payload ->> 'semantic_key'='commitment:send-report'
              AND status IN ('candidate','active')
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        authors = {
            row[2][0]["author_id"]: (row[1], row[3])
            for row in rows
        }
        assert authors["991000001"] == (["user:991000001"], 1)
        assert authors["991000002"] == (["user:991000002"], 1)

        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='group' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()



def test_explicit_first_person_group_memory_isolated_by_author_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-explicit-author-{uuid.uuid4().hex}"
    first = EventEnvelope(
        event_id=f"it:{scope_id}:u1",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880050001",
        text="Запомни: я люблю чай",
    )
    second = first.model_copy(update={
        "event_id": f"it:{scope_id}:u2",
        "occurred_at": NOW + timedelta(minutes=1),
        "actor_user_id": "991000002",
        "message_id": "880050002",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)))
        first_result = mapper.map_event(first)
        second_result = mapper.map_event(second)

        assert len(first_result.written) == 1
        assert len(second_result.written) == 1
        assert first_result.written[0].id != second_result.written[0].id

        rows = conn.execute(
            """
            SELECT subject_keys, evidence
            FROM memory_cards
            WHERE scope_type='group' AND scope_id=%s
              AND status IN ('candidate','active')
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        authors = {
            row[1][0]["author_id"]: row[0]
            for row in rows
        }
        assert authors == {
            "991000001": ["user:991000001"],
            "991000002": ["user:991000002"],
        }

        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='group' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()


def test_rejected_non_edit_candidate_is_partial_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-partial-provenance-{uuid.uuid4().hex}"
    message_id = "880050101"

    def partial_candidate(key: str, excerpt: str) -> dict:
        return {
            "memory_type": "observation",
            "semantic_key": key,
            "summary": "Синтетический факт.",
            "subject_keys": ["user:991000001"],
            "source_message_id": message_id,
            "evidence_excerpt": excerpt,
            "payload": {
                "statement_kind": "none",
                "status": "unknown",
                "due_at": None,
                "verbatim": excerpt,
                "claim_kind": "fact",
            },
            "importance": 0.6,
            "confidence": 0.6,
            "usage_policy": {
                "assist": True,
                "callback": False,
                "roast": False,
                "proactive": False,
            },
        }

    adapter = _EditAdapter([{
        "candidates": [
            partial_candidate("fact:valid", "Сохрани первый факт"),
            partial_candidate("fact:invalid", "Фрагмента нет в сообщении"),
        ]
    }])
    source = EventEnvelope(
        event_id=f"it:{scope_id}:source",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id=message_id,
        text="Сохрани первый факт",
    )

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        result = mapper.map_event(source)

        assert result.failed is True
        assert result.reason == "mapper_rejected_candidate_provenance"
        assert len(result.written) == 1
        receipt = personal_operation_receipts(result)["memory"]
        assert receipt["status"] == "partial"
        assert receipt["changed"] is True

        rows = conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 1

        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()
