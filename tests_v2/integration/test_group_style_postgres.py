from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.repositories.group_style_repo import GroupStyleRepository
from app_v2.services.group_style import GroupStyleService


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
BASE = {
    "brevity": 5,
    "humor": 5,
    "roast": 5,
    "warmth": 5,
    "playfulness": 5,
    "callback": 5,
    "initiative": 5,
}


def _database_url() -> str:
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pytest.importorskip("psycopg")
    return url


def test_group_style_latest_reaction_delivery_isolation_and_decay_postgres() -> None:
    url = _database_url()
    import psycopg

    run_migrations(url)
    suffix = uuid.uuid4().hex[:12]
    scope_a = f"-9100463{suffix[:5]}"
    scope_b = f"-9100464{suffix[:5]}"

    with psycopg.connect(url) as conn:
        def intervention(scope_id: str, mode: str, *, sent: bool, created_at: datetime) -> int:
            row = conn.execute(
                """
                INSERT INTO interventions(
                    event_id, scope_type, scope_id, primary_action, mode,
                    intervention_score, reason_codes, policy_version,
                    selected_memory_ids, generated_text, metadata, created_at
                )
                VALUES (NULL, 'group', %s, 'reply', %s, 80,
                        %s::jsonb, 'test', '[]'::jsonb, 'synthetic', '{}'::jsonb, %s)
                RETURNING id
                """,
                (
                    scope_id,
                    mode,
                    json.dumps(["roast_opportunity"] if mode == "group_roast" else []),
                    created_at,
                ),
            ).fetchone()
            intervention_id = int(row[0])
            conn.execute(
                """
                INSERT INTO outbox(
                    dedupe_key, channel, destination_id, payload, status, created_at, sent_at
                )
                VALUES (%s, 'telegram', %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    f"it:46c:{suffix}:{intervention_id}",
                    scope_id,
                    json.dumps({"metadata": {"intervention_id": str(intervention_id)}}),
                    "sent" if sent else "failed",
                    created_at,
                    created_at if sent else None,
                ),
            )
            return intervention_id

        def reaction(
            scope_id: str,
            intervention_id: int,
            actor: str,
            feedback_type: str,
            update_id: int,
            occurred_at: datetime,
        ) -> None:
            family = (
                ["approval_support"]
                if feedback_type == "reaction_positive"
                else ["explicit_negative"]
                if feedback_type == "reaction_negative"
                else []
            )
            conn.execute(
                """
                INSERT INTO feedback_events(
                    feedback_id, intervention_id, scope_id, user_id,
                    feedback_type, value, payload, created_at
                )
                VALUES (%s, %s, %s, NULL, %s, %s, %s::jsonb, %s)
                """,
                (
                    f"it:46c:{suffix}:{scope_id}:{intervention_id}:{actor}:{update_id}",
                    intervention_id,
                    scope_id,
                    feedback_type,
                    1.0 if feedback_type == "reaction_positive" else -1.0 if feedback_type == "reaction_negative" else 0.0,
                    json.dumps(
                        {
                            "source_event_id": f"tg:{update_id}",
                            "reactor_key": f"actor_chat:{actor}",
                            "reaction_families": family,
                        }
                    ),
                    occurred_at,
                ),
            )

        roast_a = intervention(scope_a, "group_roast", sent=True, created_at=NOW - timedelta(days=2))
        roast_b = intervention(scope_b, "group_roast", sent=True, created_at=NOW - timedelta(days=2))
        failed_a = intervention(scope_a, "group_roast", sent=False, created_at=NOW - timedelta(days=2))
        old_a = intervention(scope_a, "group_roast", sent=True, created_at=NOW - timedelta(days=40))

        # Actor A's old positive is explicitly removed and therefore must vanish.
        reaction(scope_a, roast_a, "a", "reaction_positive", 100, NOW - timedelta(hours=3))
        reaction(scope_a, roast_a, "a", "reaction_removed", 101, NOW - timedelta(hours=2))

        # Two current independent positives are enough for a slow +1.
        reaction(scope_a, roast_a, "b", "reaction_positive", 110, NOW - timedelta(hours=2))
        reaction(scope_a, roast_a, "c", "reaction_positive", 120, NOW - timedelta(hours=1))

        # Negative feedback for another group must not leak.
        reaction(scope_b, roast_b, "x", "reaction_negative", 130, NOW - timedelta(hours=1))

        # Feedback on a delivery that never reached Telegram is unknown.
        reaction(scope_a, failed_a, "y", "reaction_negative", 140, NOW - timedelta(minutes=50))

        # Old durable evidence ages out of the 30-day style window.
        reaction(scope_a, old_a, "old1", "reaction_positive", 10, NOW - timedelta(days=35))
        reaction(scope_a, old_a, "old2", "reaction_positive", 11, NOW - timedelta(days=35))
        conn.commit()

        repo = GroupStyleRepository(conn)
        service = GroupStyleService(repo)

        first = service.evaluate(scope_a, base_profile=BASE, now=NOW)
        other = service.evaluate(scope_b, base_profile=BASE, now=NOW)

        assert first.deltas["roast"] == 1
        assert first.evidence["banter_roast"]["positive_actors"] == 2
        assert first.evidence["banter_roast"]["negative_actors"] == 0
        assert other.deltas["roast"] == -2

        # Actor B replaces its positive reaction with a newer negative.
        reaction(scope_a, roast_a, "b", "reaction_negative", 150, NOW - timedelta(minutes=20))
        conn.commit()

        replaced = service.evaluate(scope_a, base_profile=BASE, now=NOW)
        assert replaced.deltas["roast"] == -2
        assert replaced.evidence["banter_roast"]["positive_actors"] == 1
        assert replaced.evidence["banter_roast"]["negative_actors"] == 1

        conn.execute(
            "DELETE FROM feedback_events WHERE scope_id IN (%s, %s)",
            (scope_a, scope_b),
        )
        conn.execute(
            "DELETE FROM outbox WHERE destination_id IN (%s, %s)",
            (scope_a, scope_b),
        )
        conn.execute(
            "DELETE FROM interventions WHERE scope_id IN (%s, %s)",
            (scope_a, scope_b),
        )
        conn.commit()


def test_group_style_delivered_mode_and_attributable_social_ack_postgres() -> None:
    url = _database_url()
    import psycopg

    run_migrations(url)
    suffix = uuid.uuid4().hex[:10]
    scope_id = f"-9100465{suffix[:5]}"

    with psycopg.connect(url) as conn:
        def add_intervention(
            *,
            mode: str,
            reasons: list[str],
            metadata: dict,
            label: str,
        ) -> int:
            row = conn.execute(
                """
                INSERT INTO interventions(
                    event_id, scope_type, scope_id, primary_action, mode,
                    intervention_score, reason_codes, policy_version,
                    selected_memory_ids, generated_text, metadata, created_at
                )
                VALUES (NULL, 'group', %s, 'reply', %s, 85,
                        %s::jsonb, 'test', '[]'::jsonb, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    scope_id,
                    mode,
                    json.dumps(reasons),
                    label,
                    json.dumps(metadata),
                    NOW - timedelta(hours=2),
                ),
            ).fetchone()
            intervention_id = int(row[0])
            conn.execute(
                """
                INSERT INTO outbox(
                    dedupe_key, channel, destination_id, payload,
                    status, created_at, sent_at
                )
                VALUES (%s, 'telegram', %s, %s::jsonb, 'sent', %s, %s)
                """,
                (
                    f"it:46c:family:{suffix}:{intervention_id}",
                    scope_id,
                    json.dumps({"metadata": {"intervention_id": str(intervention_id)}}),
                    NOW - timedelta(hours=2),
                    NOW - timedelta(hours=2),
                ),
            )
            return intervention_id

        def positive(intervention_id: int, actor: str, update_id: int) -> None:
            conn.execute(
                """
                INSERT INTO feedback_events(
                    feedback_id, intervention_id, scope_id, user_id,
                    feedback_type, value, payload, created_at
                )
                VALUES (%s, %s, %s, NULL, 'reaction_positive', 1.0, %s::jsonb, %s)
                """,
                (
                    f"it:46c:family:{suffix}:{intervention_id}:{actor}",
                    intervention_id,
                    scope_id,
                    json.dumps(
                        {
                            "source_event_id": f"tg:{update_id}",
                            "reactor_key": f"actor_chat:{actor}",
                            "reaction_families": ["approval_support"],
                        }
                    ),
                    NOW - timedelta(minutes=20),
                ),
            )

        callback = add_intervention(
            mode="group_callback",
            reasons=["callback_opportunity", "roast_opportunity"],
            metadata={},
            label="callback",
        )
        social_ack = add_intervention(
            mode="group_direct_reply",
            reasons=["direct_mention"],
            metadata={"feedback_family": "social_ack"},
            label="ack fallback",
        )
        positive(callback, "callback-a", 210)
        positive(callback, "callback-b", 211)
        positive(social_ack, "ack-a", 220)
        positive(social_ack, "ack-b", 221)
        conn.commit()

        state = GroupStyleService(GroupStyleRepository(conn)).evaluate(
            scope_id,
            base_profile=BASE,
            now=NOW,
        )

        assert state.deltas["callback"] == 1
        assert "roast" not in state.deltas
        assert state.deltas["brevity"] == 1
        assert state.deltas["warmth"] == 1
        assert state.evidence["callback"]["positive_actors"] == 2
        assert state.evidence["social_ack"]["positive_actors"] == 2

        conn.execute("DELETE FROM feedback_events WHERE scope_id=%s", (scope_id,))
        conn.execute("DELETE FROM outbox WHERE destination_id=%s", (scope_id,))
        conn.execute("DELETE FROM interventions WHERE scope_id=%s", (scope_id,))
        conn.commit()
