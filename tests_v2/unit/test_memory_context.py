from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.message_repo import HotMessage, MessageRepository
from app_v2.services.context_builder import ContextBuilder


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result(self.rows)


class FakeMessageRepo:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def recent_before_event(self, scope_type, scope_id, **kwargs):
        self.calls.append((scope_type, scope_id, kwargs))
        return list(self.rows)


class NoopRetrieval:
    def retrieve(self, *args, **kwargs):
        return []


def event(*, scope=ScopeType.GROUP, scope_id="-1001", message_id="20", metadata=None):
    return EventEnvelope(
        event_id="evt:20",
        event_type=EventType.GROUP_MESSAGE if scope is ScopeType.GROUP else EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=scope,
        scope_id=scope_id,
        actor_user_id="123",
        message_id=message_id,
        text="Да, я тоже",
        metadata=metadata or {},
    )


def test_message_repo_uses_scope_and_pre_event_tie_break() -> None:
    rows = [
        ("19", 456, "предыдущее", NOW - timedelta(seconds=1), None),
        ("18", 789, "ещё раньше", NOW - timedelta(minutes=1), None),
    ]
    conn = FakeConn(rows)
    repo = MessageRepository(conn)

    result = repo.recent_before_event(
        ScopeType.GROUP,
        "-1001",
        before=NOW,
        before_message_id="20",
        limit=12,
    )

    sql, params = conn.calls[0]
    assert "c.chat_type=%s" in sql
    assert "c.telegram_chat_id::text=%s" in sql
    assert "m.created_at < %s" in sql
    assert "m.telegram_message_id < %s" in sql
    assert params[0:2] == ("group", "-1001")
    assert params[-2:] == (20, 12)
    assert [item.message_id for item in result] == ["18", "19"]


def test_message_repo_invalid_boundary_id_fails_closed_to_strict_time() -> None:
    conn = FakeConn([])
    MessageRepository(conn).recent_before_event(
        ScopeType.PERSONAL,
        "123",
        before=NOW,
        before_message_id="not-a-telegram-id",
    )
    sql, params = conn.calls[0]
    assert "m.telegram_message_id <" not in sql
    assert params[0:2] == ("private", "123")
    assert params[2] == NOW


def test_context_builder_mapper_context_is_bounded_and_keeps_reply_metadata() -> None:
    rows = [
        HotMessage("10", "a", "старое", NOW - timedelta(minutes=2), None),
        HotMessage("11", "b", "люблю настольные игры", NOW - timedelta(minutes=1), "10"),
    ]
    repo = FakeMessageRepo(rows)
    builder = ContextBuilder(message_repo=repo, retrieval_engine=NoopRetrieval())

    context = builder.mapper_context(event())

    assert [item["message_id"] for item in context] == ["10", "11"]
    assert context[-1]["author_user_id"] == "b"
    assert context[-1]["reply_to_message_id"] == "10"
    call = repo.calls[0]
    assert call[0] is ScopeType.GROUP
    assert call[1] == "-1001"
    assert call[2]["before"] == NOW
    assert call[2]["before_message_id"] == "20"


def test_forum_topic_context_fails_closed_without_persisted_thread_id() -> None:
    repo = FakeMessageRepo([
        HotMessage("19", "a", "другая тема", NOW - timedelta(seconds=1), None),
    ])
    builder = ContextBuilder(message_repo=repo, retrieval_engine=NoopRetrieval())

    context = builder.mapper_context(event(metadata={"message_thread_id": 77}))

    assert context == ()
    assert repo.calls == []
