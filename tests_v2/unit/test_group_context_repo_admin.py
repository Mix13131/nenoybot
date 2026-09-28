from __future__ import annotations

import json
from datetime import datetime, timezone

from app_v2.repositories.group_context_repo import GroupContextRepository


class FakeResult:
    def __init__(self, *, one=None, all_rows=None) -> None:
        self.one = one
        self.all_rows = list(all_rows or [])

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class FakeConn:
    def __init__(self) -> None:
        self.calls = []
        self.commits = 0
        self.next_result = FakeResult(one=(7,))

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.next_result

    def commit(self):
        self.commits += 1


def test_configure_friends_test_is_atomic_single_update() -> None:
    conn = FakeConn()
    repo = GroupContextRepository(conn)
    profile = {"profile": "friends", "initiative": 3}
    assert repo.configure_friends_test("-100777", profile=profile, enabled=True) is True
    assert conn.commits == 1
    sql, params = conn.calls[0]
    assert "group_profile" in sql
    assert "is_whitelisted" in sql
    assert "silent_until = NULL" in sql
    assert json.loads(params[0]) == profile
    assert params[1:3] == (True, True)
    assert params[3] == -100777


def test_configure_disabled_friends_preserves_block_provenance() -> None:
    conn = FakeConn()
    assert GroupContextRepository(conn).configure_friends_test("-100777", profile={}, enabled=False)
    sql, params = conn.calls[0]
    assert "group_access_blocked = NOT %s" in sql
    assert "group_legacy_reconcile_pending = FALSE" in sql
    assert params[1:3] == (False, False)


def test_configure_unknown_group_returns_false() -> None:
    conn = FakeConn()
    conn.next_result = FakeResult(one=None)
    repo = GroupContextRepository(conn)
    assert repo.configure_friends_test("-100999", profile={"profile": "friends"}) is False
    assert conn.commits == 1


def test_list_groups_returns_admin_records_and_clamps_limit() -> None:
    conn = FakeConn()
    now = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    conn.next_result = FakeResult(all_rows=[(-100777, "Friends", False, True, {"profile": "friends"}, now)])
    rows = GroupContextRepository(conn).list_groups(limit=1000)
    assert len(rows) == 1
    assert rows[0].telegram_chat_id == "-100777"
    assert rows[0].profile == {"profile": "friends"}
    assert conn.calls[0][1] == (100,)


def test_find_groups_by_exact_title_has_no_admin_list_limit() -> None:
    conn = FakeConn()
    now = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    conn.next_result = FakeResult(all_rows=[(-1001, "Same", True, True, {"profile": "friends"}, now), (-1002, "Same", True, True, {"profile": "friends"}, now)])
    rows = GroupContextRepository(conn).find_groups_by_exact_title(" Same ")
    assert len(rows) == 2
    sql, params = conn.calls[0]
    assert "BTRIM" in sql
    assert "LIMIT" not in sql.upper()
    assert params == ("Same",)
