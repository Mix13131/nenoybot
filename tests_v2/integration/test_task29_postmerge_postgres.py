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



def _generic_candidate(*, source_message_id, semantic_key, excerpt, memory_type="observation"):
    return {
        "memory_type": memory_type,
        "semantic_key": semantic_key,
        "summary": excerpt,
        "subject_keys": [],
        "source_message_id": source_message_id,
        "evidence_excerpt": excerpt,
        "payload": {
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": None,
            "claim_kind": "fact",
        },
        "importance": 0.5,
        "confidence": 0.6,
        "usage_policy": {
            "assist": True,
            "callback": True,
            "roast": False,
            "proactive": False,
        },
    }


def test_multi_source_retry_uses_source_specific_slot_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-source-slot-{uuid.uuid4().hex}"
    source_a = EventEnvelope(
        event_id=f"{scope_id}:a",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880500001",
        text="Альфа отдельно. Общий статус готов.",
    )
    source_b = EventEnvelope(
        event_id=f"{scope_id}:b",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880500002",
        text="Общий статус готов.",
    )
    adapter = _Adapter([
        {"candidates": [
            _generic_candidate(
                source_message_id="880500001",
                semantic_key="alpha:separate",
                excerpt="Альфа отдельно",
            ),
            _generic_candidate(
                source_message_id="880500001",
                semantic_key="shared:ready",
                excerpt="Общий статус готов",
            ),
        ]},
        {"candidates": [
            _generic_candidate(
                source_message_id="880500002",
                semantic_key="shared:ready",
                excerpt="Общий статус готов",
            ),
        ]},
        {"candidates": [
            _generic_candidate(
                source_message_id="880500001",
                semantic_key="alpha:separate",
                excerpt="Альфа отдельно",
            ),
            _generic_candidate(
                source_message_id="880500001",
                semantic_key="shared:drifted",
                excerpt="статус готов",
                memory_type="plan",
            ),
        ]},
    ])

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(
            store=MemoryMapperStore(MemoryRepository(conn)),
            adapter=adapter,
        )
        first = mapper.map_event(source_a)
        assert len(first.written) == 2
        merged = mapper.map_event(source_b)
        assert len(merged.written) == 1

        before = conn.execute(
            """
            SELECT id, payload, evidence, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND payload ->> 'semantic_key'='shared:ready'
            """,
            (scope_id,),
        ).fetchone()
        assert before is not None
        slots = before[1]["source_candidate_slots"]
        evidence_by_message = {str(item["message_id"]): item for item in before[2]}
        assert slots[mapper._source_slot_key(
            mapper._candidate_evidence(source_a, {
                **_generic_candidate(
                    source_message_id="880500001",
                    semantic_key="shared:ready",
                    excerpt="Общий статус готов",
                ),
                "_source_candidate_index": 1,
            }, ())
        )] == 1
        assert len(evidence_by_message) == 2

        retried = mapper.map_event(source_a)
        assert retried.written == ()

        after = conn.execute(
            """
            SELECT id, payload, evidence, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND id=%s
            """,
            (scope_id, before[0]),
        ).fetchone()
        assert after == before
        assert conn.execute(
            """
            SELECT COUNT(*) FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND status IN ('candidate','active')
            """,
            (scope_id,),
        ).fetchone()[0] == 2

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()


def test_partial_retry_recovers_missing_candidate_after_slot_renumber_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-partial-renumber-{uuid.uuid4().hex}"
    source = EventEnvelope(
        event_id=f"{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880600001",
        text="Первый факт. Второй факт.",
    )
    first_candidate = _generic_candidate(
        source_message_id="880600001",
        semantic_key="partial:first",
        excerpt="Первый факт",
    )
    second_candidate = _generic_candidate(
        source_message_id="880600001",
        semantic_key="partial:second",
        excerpt="Второй факт",
        memory_type="plan",
    )

    with psycopg.connect(database_url) as conn:
        class _FailSecondCreateStore(MemoryMapperStore):
            def __init__(self, repo):
                super().__init__(repo)
                self.creates = 0
                self.fail = True

            def create(self, card):
                self.creates += 1
                if self.fail and self.creates == 2:
                    raise RuntimeError("synthetic partial failure")
                return super().create(card)

        store = _FailSecondCreateStore(MemoryRepository(conn))
        mapper = MemoryMapper(
            store=store,
            adapter=_Adapter([
                {"candidates": [first_candidate, second_candidate]},
                {"candidates": [second_candidate]},
            ]),
        )

        first = mapper.map_event(source)
        assert first.failed is True
        assert len(first.written) == 1
        assert first.written[0].payload["source_candidate_count"] == 2

        store.fail = False
        retried = mapper.map_event(source)
        assert retried.failed is False
        assert len(retried.written) == 1
        assert retried.written[0].payload["semantic_key"] == "partial:second"

        rows = conn.execute(
            """
            SELECT payload ->> 'semantic_key'
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND status IN ('candidate','active')
            ORDER BY payload ->> 'semantic_key'
            """,
            (scope_id,),
        ).fetchall()
        assert [row[0] for row in rows] == ["partial:first", "partial:second"]

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()



def test_overlapping_evidence_candidates_remain_distinct_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-overlap-candidates-{uuid.uuid4().hex}"
    source = EventEnvelope(
        event_id=f"{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880700001",
        text="Поставка завтра, стоимость 900 USD.",
    )
    adapter = _Adapter([{
        "candidates": [
            _generic_candidate(
                source_message_id="880700001",
                semantic_key="shipment:tomorrow",
                excerpt="Поставка завтра, стоимость 900 USD",
                memory_type="plan",
            ),
            _generic_candidate(
                source_message_id="880700001",
                semantic_key="shipment:cost:900-usd",
                excerpt="стоимость 900 USD",
            ),
        ]
    }])

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(
            store=MemoryMapperStore(MemoryRepository(conn)),
            adapter=adapter,
        )
        result = mapper.map_event(source)

        assert result.failed is False
        assert len(result.written) == 2
        rows = conn.execute(
            """
            SELECT payload ->> 'semantic_key',
                   (payload ->> 'source_candidate_index')::int
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND status IN ('candidate','active')
            ORDER BY (payload ->> 'source_candidate_index')::int
            """,
            (scope_id,),
        ).fetchall()
        assert rows == [
            ("shipment:tomorrow", 0),
            ("shipment:cost:900-usd", 1),
        ]

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()



def test_completed_candidate_retry_exact_evidence_survives_renumbering_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-completed-renumber-{uuid.uuid4().hex}"
    source = EventEnvelope(
        event_id=f"{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880800001",
        text="Первый факт. Второй факт.",
    )
    first_candidate = _generic_candidate(
        source_message_id="880800001",
        semantic_key="completed:first",
        excerpt="Первый факт",
    )
    second_candidate = _generic_candidate(
        source_message_id="880800001",
        semantic_key="completed:second",
        excerpt="Второй факт",
        memory_type="plan",
    )
    replay_second = _generic_candidate(
        source_message_id="880800001",
        semantic_key="completed:second:drifted",
        excerpt="Второй факт",
        memory_type="observation",
    )

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(
            store=MemoryMapperStore(MemoryRepository(conn)),
            adapter=_Adapter([
                {"candidates": [first_candidate, second_candidate]},
                {"candidates": [replay_second]},
            ]),
        )
        first = mapper.map_event(source)
        assert len(first.written) == 2

        before = conn.execute(
            """
            SELECT id, payload, evidence, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert len(before) == 2

        replay = mapper.map_event(source)
        assert replay.failed is False
        assert replay.written == ()

        after = conn.execute(
            """
            SELECT id, payload, evidence, updated_at
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
            ORDER BY id
            """,
            (scope_id,),
        ).fetchall()
        assert after == before

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()



def test_partial_retry_same_exact_evidence_recovers_missing_candidate_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-partial-same-evidence-{uuid.uuid4().hex}"
    source = EventEnvelope(
        event_id=f"{scope_id}:event",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880900001",
        text="Поставка завтра, стоимость 900 USD.",
    )
    shared_excerpt = "Поставка завтра, стоимость 900 USD"
    first_candidate = _generic_candidate(
        source_message_id="880900001",
        semantic_key="partial:schedule",
        excerpt=shared_excerpt,
        memory_type="plan",
    )
    second_candidate = _generic_candidate(
        source_message_id="880900001",
        semantic_key="partial:cost",
        excerpt=shared_excerpt,
    )

    with psycopg.connect(database_url) as conn:
        class _FailSecondCreateStore(MemoryMapperStore):
            def __init__(self, repo):
                super().__init__(repo)
                self.creates = 0
                self.fail = True

            def create(self, card):
                self.creates += 1
                if self.fail and self.creates == 2:
                    raise RuntimeError("synthetic partial failure")
                return super().create(card)

        store = _FailSecondCreateStore(MemoryRepository(conn))
        mapper = MemoryMapper(
            store=store,
            adapter=_Adapter([
                {"candidates": [first_candidate, second_candidate]},
                {"candidates": [second_candidate]},
            ]),
        )

        first = mapper.map_event(source)
        assert first.failed is True
        assert len(first.written) == 1

        store.fail = False
        retry = mapper.map_event(source)
        assert retry.failed is False
        assert len(retry.written) == 1
        assert retry.written[0].payload["semantic_key"] == "partial:cost"

        keys = conn.execute(
            """
            SELECT payload ->> 'semantic_key'
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s
              AND status IN ('candidate','active')
            ORDER BY payload ->> 'semantic_key'
            """,
            (scope_id,),
        ).fetchall()
        assert [row[0] for row in keys] == ["partial:cost", "partial:schedule"]

        conn.execute("DELETE FROM memory_cards WHERE scope_id=%s", (scope_id,))
        conn.commit()
