from __future__ import annotations

from datetime import datetime, timezone

from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository


SINCE = datetime(2026, 9, 15, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, value: int):
        self.value = value
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result((self.value,))


def test_feedback_count_uses_latest_reaction_state_per_voter() -> None:
    conn = FakeConn(2)
    count = GroupInitiativeRepository(conn).count_feedback_since(
        "-100777",
        ("reaction_positive", "reaction_negative", "explicit_negative"),
        SINCE,
    )
    assert count == 2
    sql, params = conn.calls[0]
    normalized = " ".join(sql.split())
    assert "ROW_NUMBER() OVER" in normalized
    assert "PARTITION BY f.scope_id, f.intervention_id, f.user_id" in normalized
    assert "ORDER BY f.created_at DESC, f.id DESC" in normalized
    assert "f.user_id IS NOT NULL" in normalized
    assert "f.intervention_id IS NOT NULL" in normalized
    # Psycopg parameterized SQL must escape a literal percent as %% so the
    # server receives the intended LIKE 'reaction_%' pattern.
    assert "f.feedback_type LIKE 'reaction_%%'" in normalized
    assert "r.rn=1" in normalized
    assert params[0] == "-100777"
    assert params[1] == "-100777"
    assert params[2] == SINCE


def test_empty_feedback_type_list_short_circuits_without_sql() -> None:
    conn = FakeConn(99)
    count = GroupInitiativeRepository(conn).count_feedback_since(
        "-100777",
        (),
        SINCE,
    )
    assert count == 0
    assert conn.calls == []
