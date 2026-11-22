"""ARC-01..16: received-source archive acceptance on real, synthetic PostgreSQL.

No Telegram/OpenAI calls or production configuration are used. Database checks
skip explicitly when the caller has not supplied NENOY_V2_TEST_DATABASE_URL.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import Event
from typing import Any
import uuid

import pytest

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.db.migrations import run_migrations
from app_v2.repositories.maintenance_repo import MaintenanceRepository
from app_v2.repositories.source_archive_repo import (
    ArchiveIdentityConflict,
    ArchivePermissionError,
    SourceArchiveRepository,
)
from app_v2.services.event_ingestor import ingest_telegram_update


AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
TOPIC = 17
FIXTURE = Path(__file__).parents[1] / "fixtures" / "source_archive_synthetic.json"


@dataclass
class Sandbox:
    conn: Any
    url: str
    psycopg: Any
    seed: int

    @property
    def repo(self):
        return SourceArchiveRepository(self.conn)

    @property
    def chat(self):
        return -self.seed

    @property
    def author(self):
        return self.seed + 1

    def scope(self, *, chat=None, topic=TOPIC, enabled=True, at=AT - timedelta(days=1)):
        self.repo.configure_scope(chat or self.chat, topic, enabled=enabled, activated_at=at)

    def grant(self, *, author=None, chat=None, topic=TOPIC, now=AT - timedelta(hours=1), **flags):
        author = author or self.author
        values = {"retain_source": True, **flags}
        return self.repo.set_permissions(
            chat_id=chat or self.chat, message_thread_id=topic,
            author_user_id=author, actor_user_id=author, now=now, **values,
        )

    def query(self, *, author=None, chat=None, topic=TOPIC, since=AT - timedelta(days=2),
              until=AT + timedelta(days=3), **kwargs):
        author = author or self.author
        return self.repo.query_sources(
            chat_id=chat or self.chat, message_thread_id=topic,
            author_user_id=author, actor_user_id=author, since=since, until=until, **kwargs,
        )

    def update(self, n=1, *, text="synthetic text", author=None, chat=None, topic=TOPIC,
               message_id=None, at=AT, edit_at=None, **extra):
        message = {
            "message_id": message_id or n,
            "date": int(at.timestamp()),
            "from": {"id": author or self.author, "is_bot": False, "first_name": "Synthetic"},
            "chat": {"id": chat or self.chat, "type": "supergroup", "is_forum": True},
            "message_thread_id": topic, "is_topic_message": True,
        }
        if text is not None:
            message["text"] = text
        message.update(extra)
        key = "message"
        if edit_at is not None:
            key = "edited_message"
            message["edit_date"] = int(edit_at.timestamp())
        return {"update_id": self.seed + n, key: message}

    def capture(self, *args, **kwargs):
        normalized = normalize_update(self.update(*args, **kwargs))
        assert normalized is not None
        return self.repo.capture(normalized)

    def forget(self, *, message_id=None, author=None, topic=TOPIC):
        author = author or self.author
        return self.repo.forget_sources(
            chat_id=self.chat, message_thread_id=topic, author_user_id=author,
            actor_user_id=author, telegram_message_id=message_id,
        )

    def reauthorize(self, sources, **kwargs):
        return self.repo.reauthorize(
            sources, chat_id=self.chat, message_thread_id=TOPIC,
            author_user_id=self.author, actor_user_id=self.author, **kwargs,
        )


@pytest.fixture
def archive():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)
    seed = 9_000_000_000_000 + int(uuid.uuid4().hex[:10], 16) * 1_000_000
    with psycopg.connect(url) as conn:
        # Open an outer transaction: archive methods must use savepoints rather
        # than committing another caller's work.
        conn.execute("SELECT 1")
        sandbox = Sandbox(conn, url, psycopg, seed)
        try:
            yield sandbox
        finally:
            conn.rollback()
            # A few ingest/concurrency tests intentionally commit. Remove only
            # this test's unique synthetic scopes, never another suite's data.
            chats = [sandbox.chat, sandbox.chat - 1]
            conn.execute("UPDATE source_archive_messages SET latest_source_id=NULL WHERE chat_id=ANY(%s)", (chats,))
            for table in (
                "source_archive_revisions", "source_archive_messages",
                "source_archive_deletions",
                "source_archive_consent_versions", "source_archive_consents", "source_archive_scopes",
            ):
                conn.execute(f"DELETE FROM {table} WHERE chat_id=ANY(%s)", (chats,))
            conn.execute("DELETE FROM events WHERE scope_id=ANY(%s)", ([str(chat) for chat in chats],))
            conn.execute("DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE telegram_chat_id=ANY(%s))", (chats,))
            conn.execute("DELETE FROM chat_members WHERE chat_id IN (SELECT id FROM chats WHERE telegram_chat_id=ANY(%s))", (chats,))
            conn.execute("DELETE FROM chats WHERE telegram_chat_id=ANY(%s)", (chats,))
            conn.execute("DELETE FROM users WHERE telegram_user_id IN (%s,%s)", (sandbox.author, sandbox.author + 1))
            conn.commit()


def test_arc01_report_and_nonreport_are_complete_sources(archive):
    archive.scope()
    version = archive.grant()
    report = archive.capture(1, text="Учебный отчёт: закончил задачу")
    chatter = archive.capture(2, text="Обычная учебная беседа")
    rows = archive.query()
    assert report.status == chatter.status == "archived"
    assert {row.available_text for row in rows} == {"Учебный отчёт: закончил задачу", "Обычная учебная беседа"}
    assert {row.source_id for row in rows} == {report.source_id, chatter.source_id}
    assert all((row.author_user_id, row.chat_id, row.message_thread_id) ==
               (archive.author, archive.chat, TOPIC) for row in rows)
    assert all(row.consent_version == version and row.message_revision == 1 and
               row.occurred_at == AT and len(row.checksum) == 64 for row in rows)


@pytest.mark.parametrize("reason", ["provider_unavailable", "spend_breaker_tripped"])
def test_arc02_arc12_ingestion_and_retrieval_never_call_ai(archive, monkeypatch, reason):
    from app_v2.adapters.openai_adapter import OpenAIAdapter

    calls = []

    def unavailable(*args, **kwargs):
        calls.append(reason)
        raise RuntimeError(reason)

    monkeypatch.setattr(OpenAIAdapter, "__init__", unavailable)
    monkeypatch.setattr(OpenAIAdapter, "_generate", unavailable)
    monkeypatch.delenv("NENOY_V2_OPENAI_API_KEY", raising=False)
    archive.scope()
    archive.grant(historical_inference=False, auto_report_analysis=False,
                  unsolicited_group_reply=False, direct_request_group_reply=False)
    archive.conn.commit()
    for n in range(1, 4):
        result = ingest_telegram_update(archive.update(n), archive.url, archive_enabled=True)
        assert result.status == "accepted"
    assert len(archive.query()) == 3
    assert archive.query(purpose="historical_inference") == []
    assert calls == []


def test_arc03_unconsenting_author_topic_and_other_group_are_not_retained(archive):
    archive.scope()
    archive.grant()
    for kwargs in ({"author": archive.author + 1}, {"topic": TOPIC + 1}, {"chat": archive.chat - 1}):
        assert archive.capture(**kwargs).status == "ignored"
    assert archive.query() == []
    assert archive.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE chat_id=%s", (archive.chat,)).fetchone()[0] == 0


def test_arc04_update_retries_have_stable_receipt_and_detect_conflicting_replay(archive):
    archive.scope()
    archive.grant()
    first = archive.capture()
    retry = archive.capture()
    assert (retry.source_id, retry.checksum, retry.message_revision) == (first.source_id, first.checksum, 1)
    assert retry.status == "duplicate"
    assert len(archive.query(history=True)) == 1
    with pytest.raises(ArchiveIdentityConflict):
        archive.capture(text="conflicting replay body")
    assert archive.query()[0].available_text == "synthetic text"


def test_arc05_revisions_preserve_lineage_and_out_of_order_edits(archive):
    archive.scope()
    archive.grant(historical_inference=True)
    last = archive.capture(3, message_id=1, text="latest", edit_at=AT + timedelta(minutes=2))
    original = archive.capture(1, text="original")
    middle = archive.capture(2, message_id=1, text="middle", edit_at=AT + timedelta(minutes=1))
    history = archive.query(history=True)
    assert len(history) == 3
    assert {row.source_id for row in history} == {last.source_id, original.source_id, middle.source_id}
    assert {row.message_revision for row in history} == {1, 2, 3}
    assert archive.query()[0].available_text == "latest"
    assert archive.query()[0].revision_at == AT + timedelta(minutes=2)
    assert archive.reauthorize(archive.query()) is True
    stale = next(row for row in history if row.available_text == "original")
    assert archive.reauthorize([stale]) is False


def test_arc06_legacy_30_day_cleanup_preserves_opted_in_archive(archive):
    archive.scope()
    archive.grant()
    archive.conn.commit()
    assert ingest_telegram_update(archive.update(), archive.url, archive_enabled=True).status == "accepted"
    MaintenanceRepository(archive.conn).expire_raw_message_text(now=AT + timedelta(days=45), retention_days=30)
    legacy = archive.conn.execute(
        "SELECT m.text FROM messages m JOIN chats c ON c.id=m.chat_id WHERE c.telegram_chat_id=%s", (archive.chat,),
    ).fetchone()
    assert legacy == (None,)
    assert archive.query()[0].available_text == "synthetic text"


def test_arc07_delete_is_scoped_truthful_and_blocks_resurrection(archive):
    archive.scope()
    archive.grant(historical_inference=True)
    archive.capture(1)
    archive.capture(2, text="keep")
    stale = [row for row in archive.query() if row.telegram_message_id == 1]
    receipt = archive.forget(message_id=1)
    assert (receipt.messages, receipt.revisions) == (1, 1)
    assert not receipt.backups_erased and not receipt.delivered_messages_erased and not receipt.legacy_stores_erased
    assert archive.forget(message_id=1).revisions == 0
    assert archive.reauthorize(stale) is False
    assert archive.capture(1).status == "deleted"
    assert archive.capture(3, message_id=1, edit_at=AT + timedelta(minutes=3)).status == "deleted"
    assert [row.available_text for row in archive.query()] == ["keep"]
    tombstone = archive.conn.execute(
        "SELECT available_text, media_reference, checksum, source_status FROM source_archive_revisions WHERE source_id=%s",
        (stale[0].source_id,),
    ).fetchone()
    assert tombstone == (None, {}, None, "deleted")


def test_arc07_revocation_purges_bodies_and_regrant_does_not_backfill(archive):
    archive.scope()
    archive.grant(historical_inference=True)
    archive.capture(1)
    stale = archive.query()
    archive.grant(retain_source=False, now=AT + timedelta(minutes=1))
    assert archive.query() == [] and archive.reauthorize(stale) is False
    assert archive.capture(2, at=AT + timedelta(minutes=2)).status == "ignored"
    archive.grant(now=AT + timedelta(minutes=3))
    assert archive.capture(1).status == "ignored"
    assert archive.capture(2, at=AT + timedelta(minutes=2)).status == "ignored"
    assert archive.capture(4, at=AT + timedelta(minutes=4)).status == "archived"
    assert len(archive.query()) == 1


@pytest.mark.parametrize("malformation", ["missing_topic", "anonymous", "bot", "private", "not_forum"])
def test_arc08_unknown_scope_and_identity_fail_closed(archive, malformation):
    archive.scope()
    archive.grant()
    update = archive.update()
    message = update["message"]
    if malformation == "missing_topic":
        message.pop("message_thread_id")
    elif malformation == "anonymous":
        message["sender_chat"] = {"id": archive.chat}
    elif malformation == "bot":
        message["from"]["is_bot"] = True
    elif malformation == "private":
        message["chat"]["type"] = "private"
    else:
        message["chat"].pop("is_forum")
    normalized = normalize_update(update)
    assert archive.repo.capture(normalized).status == "ignored"
    assert archive.query() == []


def test_arc09_media_coverage_is_truthful_and_has_no_downloaded_bytes(archive):
    archive.scope()
    archive.grant()
    archive.capture(1, text="plain")
    archive.capture(2, text=None, caption="visible caption", photo=[{"file_id": "synthetic-photo", "file_unique_id": "synthetic-unique", "file_size": 400}])
    archive.capture(3, text=None, voice={"file_id": "synthetic-voice", "duration": 3})
    archive.capture(4, text=None, document={"file_id": "synthetic-doc", "file_name": "not-copied.pdf"})
    rows = {row.telegram_message_id: row for row in archive.query()}
    assert (rows[1].content_kind, rows[1].body_coverage) == ("text", "full")
    assert (rows[2].content_kind, rows[2].body_coverage, rows[2].available_text) == ("photo", "partial", "visible caption")
    assert (rows[3].content_kind, rows[3].body_coverage, rows[3].available_text) == ("voice", "unavailable", None)
    assert rows[4].body_coverage == "unavailable"
    assert "file_name" not in rows[4].media_reference


def test_arc10_ingest_crash_rolls_back_then_retry_archives_once(archive, monkeypatch):
    from app_v2.repositories.ingest_repo import TelegramIngestRepository

    archive.scope()
    archive.grant()
    archive.conn.commit()
    original = TelegramIngestRepository.insert_event

    def crash_after_insert(self, normalized):
        original(self, normalized)
        raise RuntimeError("synthetic crash before ACK")

    with monkeypatch.context() as patch:
        patch.setattr(TelegramIngestRepository, "insert_event", crash_after_insert)
        with pytest.raises(RuntimeError, match="before ACK"):
            ingest_telegram_update(archive.update(), archive.url, archive_enabled=True)
    assert archive.query() == []
    assert archive.conn.execute("SELECT count(*) FROM events WHERE telegram_update_id=%s", (archive.seed + 1,)).fetchone()[0] == 0
    assert ingest_telegram_update(archive.update(), archive.url, archive_enabled=True).status == "accepted"
    assert ingest_telegram_update(archive.update(), archive.url, archive_enabled=True).status == "duplicate"
    assert len(archive.query()) == 1


def test_arc10_outer_transaction_rollback_does_not_ack_partial_archive(archive):
    archive.scope()
    archive.grant()
    archive.conn.commit()
    class SyntheticCrash(Exception):
        pass
    with pytest.raises(SyntheticCrash):
        with archive.conn.transaction():
            assert archive.capture().status == "archived"
            raise SyntheticCrash()
    assert archive.query() == []
    assert archive.capture().status == "archived"
    assert len(archive.query()) == 1


def test_arc13_two_participants_grant_only_their_own_independent_permissions(archive):
    archive.scope()
    first = archive.grant(historical_inference=True, direct_request_group_reply=True)
    second = archive.grant(author=archive.author + 1)
    archive.capture(1)
    archive.capture(2, author=archive.author + 1)
    assert first == second == 1
    assert len(archive.query()) == len(archive.query(author=archive.author + 1)) == 1
    assert len(archive.query(purpose="historical_inference")) == 1
    assert archive.query(author=archive.author + 1, purpose="historical_inference") == []
    with pytest.raises(ArchivePermissionError):
        archive.repo.set_permissions(chat_id=archive.chat, message_thread_id=TOPIC,
            author_user_id=archive.author + 1, actor_user_id=archive.author, retain_source=True)
    with pytest.raises(ArchivePermissionError):
        archive.repo.query_sources(chat_id=archive.chat, message_thread_id=TOPIC,
            author_user_id=archive.author + 1, actor_user_id=archive.author, since=AT, until=AT + timedelta(days=1))


def test_arc14_coverage_discloses_pre_activation_and_undetectable_delivery_gaps(archive):
    archive.scope(at=AT)
    archive.grant(now=AT)
    assert archive.capture(1, at=AT - timedelta(seconds=1)).status == "ignored"
    archive.capture(2)
    report = archive.repo.coverage(chat_id=archive.chat, message_thread_id=TOPIC,
        author_user_id=archive.author, actor_user_id=archive.author,
        since=AT - timedelta(days=100), until=AT + timedelta(days=1))
    assert report["eligible_since"] == AT and report["has_pre_activation_gap"] is True
    assert report["messages"] == report["revisions"] == 1
    assert report["complete_telegram_history"] is False
    assert report["missing_delivery_detection"] == "unavailable"
    assert report["automatic_telegram_deletion_detection"] is False
    assert report["historical_import_performed"] is False


def test_arc15_synthetic_fixture_never_launders_nested_third_party_evidence(archive):
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["fixture_kind"] == "synthetic_only"
    archive.scope()
    archive.grant(historical_inference=True)
    for offset, original in enumerate(fixture["updates"], 1):
        update = deepcopy(original)
        update["update_id"] = archive.seed + offset
        message = update.get("message") or update["edited_message"]
        message["chat"]["id"] = archive.chat
        message["from"]["id"] = archive.author
        assert archive.repo.capture(normalize_update(update)).status == "archived"
    exported = archive.query(history=True)
    assert len(exported) == 5
    assert all("DO_NOT_COPY_" not in (row.available_text or "") and
               "DO_NOT_COPY_" not in json.dumps(row.media_reference) for row in exported)
    external = [row for row in exported if row.contains_external_content]
    assert len(external) == 1 and external[0].author_user_id == archive.author
    inference = archive.query(purpose="historical_inference")
    assert not any(row.contains_external_content for row in inference)
    assert archive.reauthorize(external) is False


def test_source_snapshot_is_invalidated_by_edit_permission_change_and_disable(archive):
    archive.scope()
    archive.grant(historical_inference=True)
    archive.capture()
    snapshot = archive.query()
    assert archive.reauthorize(snapshot) is True
    archive.capture(2, message_id=1, edit_at=AT + timedelta(seconds=1))
    assert archive.reauthorize(snapshot) is False
    snapshot = archive.query()
    archive.grant(historical_inference=True, direct_request_group_reply=True, now=AT + timedelta(seconds=2))
    assert archive.reauthorize(snapshot) is False
    snapshot = archive.query()
    assert archive.reauthorize(snapshot) is True
    archive.scope(enabled=False)
    assert archive.reauthorize(snapshot) is False and archive.query() == []


def test_scope_or_author_cannot_be_reassigned_by_message_edit(archive):
    archive.scope()
    archive.scope(topic=TOPIC + 1)
    archive.grant()
    archive.grant(topic=TOPIC + 1)
    archive.grant(author=archive.author + 1)
    archive.capture()
    for kwargs in ({"topic": TOPIC + 1}, {"author": archive.author + 1}):
        with pytest.raises(ArchiveIdentityConflict):
            archive.capture(2, message_id=1, edit_at=AT + timedelta(seconds=1), **kwargs)
    assert len(archive.query()) == 1
    assert archive.query(topic=TOPIC + 1) == []


def test_concurrent_duplicate_capture_is_exactly_once(archive):
    archive.scope()
    archive.grant()
    archive.conn.commit()
    update = normalize_update(archive.update())
    def worker():
        with archive.psycopg.connect(archive.url) as conn:
            return SourceArchiveRepository(conn).capture(update)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(row.status for row in results) == ["archived", "duplicate"]
    assert results[0].source_id == results[1].source_id
    assert len(archive.query(history=True)) == 1


def test_revocation_serializes_with_in_flight_capture(archive):
    archive.scope()
    archive.grant()
    archive.conn.commit()
    started = Event()
    def capture_other_connection():
        with archive.psycopg.connect(archive.url) as conn:
            started.set()
            return SourceArchiveRepository(conn).capture(normalize_update(archive.update()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with archive.conn.transaction():
            archive.conn.execute("SELECT 1 FROM source_archive_scopes WHERE chat_id=%s FOR UPDATE", (archive.chat,))
            future = pool.submit(capture_other_connection)
            assert started.wait(5)
            archive.grant(retain_source=False, now=AT + timedelta(minutes=1))
        result = future.result(timeout=10)
    assert result.status == "ignored"
    assert archive.query() == []


def test_arc11_100k_sources_have_scoped_indexed_paginated_retrieval(archive):
    archive.conn.execute("SET LOCAL statement_timeout = '30s'")
    partitions = [(archive.chat, TOPIC, archive.author), (archive.chat, TOPIC, archive.author + 1),
                  (archive.chat, TOPIC + 1, archive.author), (archive.chat - 1, TOPIC, archive.author)]
    for chat, topic, author in partitions:
        archive.scope(chat=chat, topic=topic)
        archive.grant(chat=chat, topic=topic, author=author)
    for bucket, (chat, topic, author) in enumerate(partitions):
        # 25,000 messages in each of four author/topic/group partitions. These
        # are synthetic storage fixtures; capture permission paths are tested
        # separately above, so bulk SQL keeps this regression affordable.
        archive.conn.execute(
            """INSERT INTO source_archive_messages(chat_id,message_thread_id,author_user_id,telegram_message_id,occurred_at)
               SELECT %s,%s,%s,(n*4+%s),%s::timestamptz + n*INTERVAL '1 second'
               FROM generate_series(1,25000) n""", (chat, topic, author, bucket, AT),
        )
        archive.conn.execute("ANALYZE source_archive_messages")
        archive.conn.execute(
            """INSERT INTO source_archive_revisions(source_id,chat_id,message_thread_id,author_user_id,telegram_message_id,
                   telegram_update_id,message_revision,occurred_at,revision_at,is_edited,content_kind,available_text,body_coverage,consent_version,checksum)
               SELECT 'tgsrc:' || %s::text || ':' || %s::text || ':' || (n*4+%s)::text || ':' || (%s+n*4+%s)::text,%s,%s,%s,n*4+%s,%s+n*4+%s,
                      1,%s::timestamptz+n*INTERVAL '1 second',%s::timestamptz+n*INTERVAL '1 second',false,
                      'text','synthetic partition ' || %s::text || ' item ' || n::text,'full',1,repeat('a',64)
               FROM generate_series(1,25000) n""",
            (chat, topic, bucket, archive.seed, bucket, chat, topic, author, bucket, archive.seed, bucket, AT, AT, bucket),
        )
    archive.conn.execute(
        """UPDATE source_archive_messages m SET latest_source_id=r.source_id FROM source_archive_revisions r
           WHERE m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id AND m.chat_id=ANY(%s)""",
        ([archive.chat, archive.chat - 1],),
    )
    archive.conn.execute("ANALYZE source_archive_revisions")
    archive.conn.execute("ANALYZE source_archive_messages")
    assert archive.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE chat_id=ANY(%s)", ([archive.chat, archive.chat - 1],)).fetchone()[0] == 100000
    seen = set()
    cursor = None
    while True:
        page = archive.query(limit=200, after=cursor)
        if not page:
            break
        assert all((row.chat_id, row.message_thread_id, row.author_user_id) ==
                   (archive.chat, TOPIC, archive.author) for row in page)
        assert all(row.available_text.startswith("synthetic partition 0 ") for row in page)
        assert not seen.intersection(row.source_id for row in page)
        seen.update(row.source_id for row in page)
        cursor = (page[-1].occurred_at, page[-1].source_id)
    assert len(seen) == 25000
    bounded = archive.query(since=AT + timedelta(seconds=10), until=AT + timedelta(seconds=20))
    assert len(bounded) == 10
    class QueryRecorder:
        def __init__(self, conn):
            self.conn = conn
            self.last = None
        def execute(self, query, params):
            self.last = (query, params)
            return self.conn.execute(query, params)
    recorder = QueryRecorder(archive.conn)
    SourceArchiveRepository(recorder).query_sources(
        chat_id=archive.chat, message_thread_id=TOPIC, author_user_id=archive.author,
        actor_user_id=archive.author, since=AT + timedelta(seconds=10),
        until=AT + timedelta(seconds=20), limit=100,
    )
    # EXPLAIN the real five-table authorization/revision query, not a surrogate.
    plan = archive.conn.execute("EXPLAIN (FORMAT JSON) " + recorder.last[0], recorder.last[1]).fetchone()[0]
    plan_text = json.dumps(plan)
    # The planner may choose either scope/date ordering index for a small date
    # window. A broad export is allowed to use a sequential/hash plan; above we
    # verify its real 25,000-row paginated result without imposing a fake SLA.
    source_indexes = {name for name in ("idx_source_archive_scope_date", "idx_source_archive_scope_latest") if name in plan_text}
    assert source_indexes, json.dumps(plan, indent=2)
    print("ARC11 narrow-date indexes:", ", ".join(sorted(source_indexes)))


def test_latest_pagination_does_not_repeat_a_message_edited_between_pages(archive):
    archive.scope()
    archive.grant()
    archive.capture(1)
    archive.capture(2)
    first = archive.query(limit=1)
    assert first[0].telegram_message_id == 1
    archive.capture(3, message_id=1, text="edited after first page", edit_at=AT + timedelta(minutes=1))
    next_page = archive.query(limit=1, after=(first[0].occurred_at, first[0].source_id))
    assert [row.telegram_message_id for row in next_page] == [2]
    assert archive.query(purpose="historical_inference") == []
    with pytest.raises(ArchivePermissionError):
        archive.query(purpose="historical_inference", history=True)


def test_full_forget_sets_boundary_for_unseen_delayed_messages(archive):
    archive.scope()
    archive.grant(historical_inference=True, direct_request_group_reply=True)
    archive.capture()
    receipt = archive.repo.forget_sources(
        chat_id=archive.chat, message_thread_id=TOPIC, author_user_id=archive.author,
        actor_user_id=archive.author, now=AT + timedelta(seconds=60, microseconds=500000),
    )
    assert receipt.revisions == 1 and archive.query() == []
    assert archive.capture(2, at=AT + timedelta(seconds=20)).status == "ignored"
    assert archive.capture(3, at=AT + timedelta(seconds=60)).status == "ignored"
    assert archive.capture(4, at=AT + timedelta(seconds=61)).status == "archived"
    new = archive.query(purpose="historical_inference")
    assert len(new) == 1 and new[0].consent_version == 2
    grants = archive.conn.execute(
        """SELECT retain_source,historical_inference,auto_report_analysis,
                  unsolicited_group_reply,direct_request_group_reply
           FROM source_archive_consents WHERE chat_id=%s""", (archive.chat,),
    ).fetchone()
    assert grants == (True, True, False, False, True)


def test_configured_grants_do_not_enable_runtime_archival_by_default(archive, monkeypatch):
    monkeypatch.delenv("NENOY_V2_SOURCE_ARCHIVE_ENABLED", raising=False)
    archive.scope()
    archive.grant()
    archive.conn.commit()
    assert ingest_telegram_update(archive.update(), archive.url).status == "accepted"
    assert archive.query() == []
    assert archive.conn.execute("SELECT count(*) FROM events WHERE telegram_update_id=%s", (archive.seed + 1,)).fetchone()[0] == 1


@pytest.mark.parametrize("boundary", ["activation", "consent"])
def test_activation_and_consent_round_up_to_telegram_seconds(archive, boundary):
    boundary_at = AT + timedelta(microseconds=500000)
    archive.scope(at=boundary_at if boundary == "activation" else AT - timedelta(days=1))
    archive.grant(now=boundary_at if boundary == "consent" else AT - timedelta(hours=1))
    assert archive.capture(1, at=AT).status == "ignored"
    assert archive.capture(2, at=AT + timedelta(seconds=1)).status == "archived"


def test_reenabled_topic_excludes_messages_received_during_disabled_window(archive):
    archive.scope()
    archive.grant()
    archive.capture(1)
    archive.scope(enabled=False, at=AT + timedelta(seconds=10))
    assert archive.capture(2, at=AT + timedelta(seconds=20)).status == "ignored"
    archive.scope(enabled=True, at=AT + timedelta(seconds=30, microseconds=500000))
    assert archive.capture(2, at=AT + timedelta(seconds=20)).status == "ignored"
    assert archive.capture(3, at=AT + timedelta(seconds=30)).status == "ignored"
    assert archive.capture(4, at=AT + timedelta(seconds=31)).status == "archived"
    assert {row.telegram_message_id for row in archive.query()} == {1, 4}


def test_specific_unseen_forget_records_intent_and_blocks_delayed_capture(archive):
    archive.scope()
    archive.grant()
    receipt = archive.repo.forget_sources(
        chat_id=archive.chat, message_thread_id=TOPIC, author_user_id=archive.author,
        actor_user_id=archive.author, telegram_message_id=123,
        now=AT + timedelta(minutes=1),
    )
    assert (receipt.messages, receipt.revisions) == (0, 0)
    assert receipt.deletion_intent_recorded is True
    assert not receipt.backups_erased and not receipt.delivered_messages_erased
    retry = archive.repo.forget_sources(
        chat_id=archive.chat, message_thread_id=TOPIC, author_user_id=archive.author,
        actor_user_id=archive.author, telegram_message_id=123,
        now=AT + timedelta(minutes=2),
    )
    assert retry.messages == retry.revisions == 0
    assert retry.deletion_intent_recorded is True
    assert archive.conn.execute(
        "SELECT count(*) FROM source_archive_deletions WHERE chat_id=%s", (archive.chat,),
    ).fetchone()[0] == 1
    assert archive.capture(1, message_id=123).status == "deleted"
    assert archive.capture(1, message_id=123).status == "deleted"
    assert archive.query(history=True) == []
    assert archive.capture(2, message_id=124).status == "archived"
    assert [row.telegram_message_id for row in archive.query()] == [124]


def test_specific_unseen_forget_survives_revoke_and_regrant(archive):
    archive.scope()
    archive.grant()
    archive.repo.forget_sources(
        chat_id=archive.chat, message_thread_id=TOPIC, author_user_id=archive.author,
        actor_user_id=archive.author, telegram_message_id=123, now=AT,
    )
    archive.grant(retain_source=False, now=AT + timedelta(seconds=1))
    archive.grant(now=AT + timedelta(seconds=2))
    # Even an adversarial retry claiming a later creation time cannot erase
    # the durable deletion intent for this exact author/topic/message identity.
    assert archive.capture(1, message_id=123, at=AT + timedelta(seconds=3)).status == "deleted"
    assert archive.conn.execute(
        "SELECT count(*) FROM source_archive_deletions WHERE chat_id=%s", (archive.chat,),
    ).fetchone()[0] == 1
    assert archive.query(history=True) == []


@pytest.mark.parametrize("other_scope", ["author", "topic", "chat"])
def test_specific_unseen_forget_does_not_block_other_scope(archive, other_scope):
    archive.scope()
    archive.grant()
    archive.forget(message_id=123)
    if other_scope == "author":
        kwargs = {"author": archive.author + 1}
        archive.grant(**kwargs)
    elif other_scope == "topic":
        kwargs = {"topic": TOPIC + 1}
        archive.scope(**kwargs)
        archive.grant(**kwargs)
    else:
        kwargs = {"chat": archive.chat - 1}
        archive.scope(**kwargs)
        archive.grant(**kwargs)
    assert archive.capture(1, message_id=123, **kwargs).status == "archived"
    assert archive.query() == []
    assert [row.telegram_message_id for row in archive.query(**kwargs)] == [123]


def test_specific_unseen_forget_requires_existing_participant_consent(archive):
    archive.scope()
    with pytest.raises(ArchivePermissionError):
        archive.forget(message_id=123)
    assert archive.conn.execute(
        "SELECT count(*) FROM source_archive_deletions WHERE chat_id=%s", (archive.chat,),
    ).fetchone()[0] == 0


def test_specific_unseen_forget_serializes_before_in_flight_capture(archive):
    archive.scope()
    archive.grant()
    archive.conn.commit()
    started = Event()

    def capture_other_connection():
        with archive.psycopg.connect(archive.url) as conn:
            started.set()
            return SourceArchiveRepository(conn).capture(normalize_update(archive.update(message_id=123)))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with archive.conn.transaction():
            archive.conn.execute(
                """SELECT 1 FROM source_archive_consents
                   WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s FOR UPDATE""",
                (archive.chat, TOPIC, archive.author),
            )
            future = pool.submit(capture_other_connection)
            assert started.wait(5)
            receipt = archive.forget(message_id=123)
            assert receipt.messages == receipt.revisions == 0
            assert receipt.deletion_intent_recorded is True
        assert future.result(timeout=10).status == "deleted"
    assert archive.query(history=True) == []
