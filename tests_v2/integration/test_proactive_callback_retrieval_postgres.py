from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import ScopeType
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.services.retrieval_engine import RetrievalEngine


def _database_url() -> str:
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pytest.importorskip("psycopg")
    return url


def test_callback_proactive_filter_runs_before_ranking_and_keeps_fatigue_postgres() -> None:
    url = _database_url()
    import psycopg

    run_migrations(url)
    scope_id = f"it-proactive-retrieval-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)

    with psycopg.connect(url) as conn:
        def insert_card(
            memory_id: str,
            *,
            proactive: bool,
            importance: float,
            last_used_at: datetime | None = None,
        ) -> None:
            conn.execute(
                """
                INSERT INTO memory_cards(
                    id, scope_type, scope_id, memory_type, subject_keys, summary,
                    importance, confidence, freshness, status, origin,
                    usage_policy, evidence, source_count, last_used_at
                )
                VALUES (
                    %s, 'group', %s, 'running_joke', ARRAY[]::text[], %s,
                    %s, 0.95, 0.95, 'active', 'system',
                    %s::jsonb, '[]'::jsonb, 1, %s
                )
                """,
                (
                    memory_id,
                    scope_id,
                    f"synthetic {memory_id}",
                    importance,
                    json.dumps(
                        {
                            "assist": True,
                            "callback": True,
                            "roast": True,
                            "proactive": proactive,
                        }
                    ),
                    last_used_at,
                ),
            )

        # These rank above the allowed card, but must be removed in SQL before LIMIT.
        for index in range(6):
            insert_card(
                f"{scope_id}:blocked:{index}",
                proactive=False,
                importance=1.0 - index * 0.01,
            )

        allowed_id = f"{scope_id}:allowed"
        insert_card(allowed_id, proactive=True, importance=0.40)

        # This card is proactive but still inside callback fatigue and must stay out.
        fatigued_id = f"{scope_id}:fatigued"
        insert_card(
            fatigued_id,
            proactive=True,
            importance=1.0,
            last_used_at=now - timedelta(minutes=10),
        )
        conn.commit()

        result = RetrievalEngine(MemoryRepository(conn)).retrieve(
            ScopeType.GROUP,
            scope_id,
            usage="callback",
            require_proactive=True,
            callback_fatigue_minutes=180,
            limit=6,
            expand_relations=False,
        )

        ids = [item.card.id for item in result]
        assert allowed_id in ids
        assert fatigued_id not in ids
        assert all(item.card.usage_policy.proactive for item in result)
        assert not any(":blocked:" in memory_id for memory_id in ids)

        conn.execute(
            "DELETE FROM memory_cards WHERE scope_type='group' AND scope_id=%s",
            (scope_id,),
        )
        conn.commit()
