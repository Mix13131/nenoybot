"""Historical snapshots coexist with live intake and cannot revive erased data.

Every test uses a dedicated, disposable PostgreSQL database and synthetic input.
No real export, credentials, Telegram message or model request is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from queue import Queue
import time
from types import SimpleNamespace

import pytest

from app_v2.adapters.telegram_history import parse_telegram_desktop_export
from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.domain.coach import SourceProof
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.coach_scope_repo import CoachMembership, CoachScopeRepository
from app_v2.repositories.source_archive_repo import (
    ArchiveIdentityConflict, ArchivePermissionError, SourceArchiveRepository,
)
from app_v2.repositories.source_history_import_repo import SourceHistoryImportRepository
from app_v2.services.coach_history_import import import_history
from app_v2.services.coach_ingestor import ingest_coach_update
from app_v2.services.coach_sources import CoachSourceSelector
from tests_v2.integration.test_coach_lifecycle_postgres import dedicated_database


BOT, AUTHOR = 907070001, 907070002
RAW_CHAT, CHAT, TOPIC = 87000, -1000000087000, 17


class SyntheticTelegram:
    def __init__(self):
        self.checked_authors = []

    def get_me(self):
        return {"id": BOT, "username": "history_synthetic_bot", "is_bot": True,
                "can_read_all_group_messages": True}

    def check_group_access(self, **kwargs):
        assert kwargs == {"chat_id": CHAT, "bot_user_id": BOT,
                          "can_read_all_group_messages": True}

    def history_author_is_human(self, *, chat_id, user_id):
        assert chat_id == CHAT
        self.checked_authors.append(user_id)
        return user_id != BOT


@pytest.fixture
def history(dedicated_database):
    pg, url = dedicated_database
    config = CoachConfig(
        environment="test", enabled=True, ai_enabled=True, group_mode="joined_groups",
        database_url=url, telegram_bot_token=f"{BOT}:synthetic_not_a_live_token",
        webhook_secret="synthetic_history_secret_" + "s" * 32, openai_api_key="synthetic",
        bot_user_id=BOT, bot_username="history_synthetic_bot", ai_model="synthetic-model",
        daily_cap_microusd=10_000_000, input_price_microusd_per_million=1_000_000,
        output_price_microusd_per_million=2_000_000,
        max_input_tokens=500_000, max_output_tokens=2000,
        archive_policy="until_author_deletes",
    )
    prepare(config)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    at = now - timedelta(days=3)
    with pg.connect(url, autocommit=True) as conn:
        archive = SourceArchiveRepository(conn, allow_group_messages=True)
        importer = SourceHistoryImportRepository(conn, bot_user_id=BOT)

        def message(identifier=100, *, author=AUTHOR, topic=TOPIC, text="Synthetic old report",
                    occurred_at=at, revision_at=None, **extra):
            result = {"id": identifier, "type": "message", "from_id": f"user{author}",
                      "date_unixtime": str(int(occurred_at.timestamp())),
                      "reply_to_message_id": topic, "text": text, **extra}
            if revision_at is not None:
                result["edited_unixtime"] = str(int(revision_at.timestamp()))
            return result

        def payload(*messages, name="Synthetic group"):
            messages = messages or (message(),)
            topics = sorted({m.get("message_thread_id", m["reply_to_message_id"]) for m in messages})
            roots = [{"id": topic, "type": "service", "action": "topic_created",
                      "date_unixtime": str(int(at.timestamp()))} for topic in topics]
            return json.dumps({"name": name, "type": "private_supergroup", "id": RAW_CHAT,
                               "messages": roots + list(messages)}, ensure_ascii=False).encode("utf-8")

        def apply(*messages, **kwargs):
            return importer.import_export(parse_telegram_desktop_export(payload(*messages, **kwargs)), now=now)

        def query(*, author=AUTHOR, topic=TOPIC, **kwargs):
            return archive.query_sources(chat_id=CHAT, message_thread_id=topic, author_user_id=author,
                                         actor_user_id=author, since=at-timedelta(days=1),
                                         until=now+timedelta(days=1), **kwargs)

        def live(identifier=100, *, author=AUTHOR, topic=TOPIC, text="Synthetic live report",
                 occurred_at=at, revision_at=None, update_id=5001):
            value = {"message_id": identifier, "from": {"id": author, "is_bot": False},
                     "chat": {"id": CHAT, "type": "supergroup", "is_forum": True},
                     "date": int(occurred_at.timestamp()), "text": text,
                     "message_thread_id": topic, "is_topic_message": True}
            if revision_at is not None:
                value["edit_date"] = int(revision_at.timestamp())
            return ingest_coach_update(
                {"update_id": update_id, "edited_message" if revision_at is not None else "message": value},
                conn=conn, config=config,
            )

        def forget(*, identifier=None, connection=conn):
            return SourceArchiveRepository(connection, allow_group_messages=True).forget_sources(
                chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR, actor_user_id=AUTHOR,
                telegram_message_id=identifier, now=now+timedelta(seconds=1),
            )

        yield SimpleNamespace(pg=pg, url=url, conn=conn, config=config, now=now, at=at,
                              archive=archive, importer=importer, message=message, payload=payload,
                              apply=apply, query=query, live=live, forget=forget)


def test_apply_preserves_snapshot_provenance_and_creates_no_ai_jobs_or_sends(history):
    client = SyntheticTelegram()
    raw = history.payload(history.message(revision_at=history.at+timedelta(minutes=1)),
                          history.message(101, author=AUTHOR+1))
    result = import_history(history.config, raw, expected_chat_id=CHAT, dry_run=False, client=client)
    assert result["status"] == "imported"
    assert result["counts"]["archived"] == result["counts"]["available_sources"] == 2
    assert result["counts"]["analysis_jobs_created"] == result["counts"]["telegram_messages_sent"] == 0
    assert client.checked_authors == [AUTHOR, AUTHOR+1]
    item = history.query()[0]
    assert (item.chat_id, item.message_thread_id, item.author_user_id, item.telegram_message_id) == (
        CHAT, TOPIC, AUTHOR, 100,
    )
    assert item.telegram_update_id is None
    assert item.source_origin == "historical_import" and item.import_batch_id
    assert item.source_id.startswith(f"tghist:{CHAT}:{TOPIC}:100:")
    assert item.checksum not in item.source_id
    assert item.occurred_at == history.at and item.revision_at == history.at+timedelta(minutes=1)
    assert item.is_edited and item.message_revision == 1
    for table in ("coach_jobs", "coaching_outputs", "coach_ai_operations", "coach_message_versions",
                  "coach_ingest_receipts", "events", "messages", "outbox"):
        assert history.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert history.archive.coverage(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                                    actor_user_id=AUTHOR, since=history.at-timedelta(days=1),
                                    until=history.now)["historical_import_performed"]


def test_preview_exercises_real_writes_and_rolls_back_every_import_side_effect(history):
    result = import_history(history.config, history.payload(), expected_chat_id=CHAT,
                            dry_run=True, client=SyntheticTelegram())
    assert result["status"] == "preview" and result["counts"]["archived"] == 1
    for table in ("source_archive_imports", "source_archive_messages", "source_archive_revisions",
                  "source_archive_scopes", "source_archive_consents", "source_archive_consent_versions",
                  "coach_group_memberships", "coach_participant_state"):
        assert history.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert history.apply()["archived"] == 1


def test_repeat_file_and_reexport_metadata_do_not_duplicate_sources_or_grants(history):
    assert history.apply()["archived"] == 1
    first = history.query()[0]
    assert history.apply()["duplicates"] == 1
    changed_export = history.apply(history.message(**{"from": "Synthetic new display name",
                                                       "reactions": [{"type": "emoji", "count": 1}]}),
                                   name="Synthetic renamed group")
    assert changed_export["archived"] == 0 and changed_export["duplicates"] == 1
    assert history.query()[0] == first
    assert history.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone()[0] == 1
    assert history.conn.execute("SELECT count(*) FROM source_archive_consent_versions").fetchone()[0] == 1


@pytest.mark.parametrize("live_first", [True, False])
@pytest.mark.parametrize("import_seconds", [10, 20, 30])
def test_live_and_imported_revisions_order_by_time_with_live_winning_ties(history, live_first, import_seconds):
    snapshot = history.message(text="Imported revision", revision_at=history.at+timedelta(seconds=import_seconds))
    def live():
        assert history.live(text="Live revision", revision_at=history.at+timedelta(seconds=20)).archived
    if live_first:
        live()
        history.apply(snapshot)
    else:
        history.apply(snapshot)
        live()
    latest = history.query()[0]
    assert latest.available_text == ("Imported revision" if import_seconds > 20 else "Live revision")
    assert latest.source_origin == ("historical_import" if import_seconds > 20 else "telegram_update")
    assert len(history.query(history=True)) == 2


def test_live_original_after_import_uses_real_update_identity_and_can_queue_current_request(history):
    at = history.now-timedelta(seconds=10)
    text = "@history_synthetic_bot разбери синтетическую задачу"
    history.apply(history.message(text=text, occurred_at=at))
    result = history.live(text=text, occurred_at=at)
    assert result.archived and result.queued
    row = history.conn.execute("SELECT source_id FROM coach_jobs").fetchone()
    assert row and row[0].startswith("tgsrc:")
    assert history.query()[0].telegram_update_id == 5001


def test_same_timestamp_import_conflict_rolls_back_other_rows_and_batch_metadata(history):
    history.apply()
    with pytest.raises(ArchiveIdentityConflict):
        history.apply(history.message(90, text="Must roll back"), history.message(text="Conflicting body"))
    assert [m.telegram_message_id for m in history.query()] == [100]
    assert history.conn.execute("SELECT count(*) FROM source_archive_imports").fetchone()[0] == 1
    assert history.conn.execute("SELECT count(*) FROM source_archive_messages").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["author", "topic", "time"])
def test_import_cannot_reassign_immutable_message_identity(history, change):
    history.apply()
    changes = {"author": {"author": AUTHOR+1}, "topic": {"topic": TOPIC+1},
               "time": {"occurred_at": history.at+timedelta(seconds=1)}}[change]
    with pytest.raises(ArchiveIdentityConflict):
        history.apply(history.message(**changes))
    assert len(history.query()) == 1
    assert history.conn.execute("SELECT count(*) FROM source_archive_scopes").fetchone()[0] == 1
    assert history.conn.execute("SELECT count(*) FROM source_archive_consents").fetchone()[0] == 1


def test_personal_optout_is_not_overwritten_or_refilled_by_an_operator_export(history):
    history.apply()
    history.archive.set_permissions(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                                    actor_user_id=AUTHOR, retain_source=False, now=history.now)
    result = history.apply(history.message(101))
    assert result["archived"] == 0 and result["permission_skipped"] == 1
    assert history.query() == []
    assert history.conn.execute("SELECT grant_basis,retain_source FROM source_archive_consents").fetchone() == (
        "self", False,
    )


def test_full_forget_blocks_unseen_old_messages_and_live_edit_of_erased_import(history):
    history.apply()
    history.forget()
    result = history.apply(history.message(101, text="Unseen old source"))
    assert result["permission_skipped"] == 1 and result["archived"] == 0
    assert not history.live(revision_at=history.now, text="Old message corrected now").archived
    assert history.query() == []
    assert history.conn.execute("SELECT available_text,checksum,source_status FROM source_archive_revisions").fetchall() == [
        (None, None, "deleted"),
    ]


def test_individual_unseen_message_tombstone_survives_an_import(history):
    history.apply()
    history.forget(identifier=101)
    result = history.apply(history.message(101))
    assert result["deleted"] == 1 and result["archived"] == 0
    assert [m.telegram_message_id for m in history.query()] == [100]


def test_disabled_topic_and_removed_group_are_never_reactivated_by_import(history):
    history.apply()
    history.archive.configure_scope(CHAT, TOPIC, enabled=False, activated_at=history.now)
    assert history.apply(history.message(101))["permission_skipped"] == 1
    assert history.conn.execute("SELECT enabled FROM source_archive_scopes").fetchone() == (False,)
    registry = CoachScopeRepository(history.conn, bot_user_id=BOT)
    registry.apply_membership(CoachMembership(CHAT, 7000, history.now, False, True))
    with pytest.raises(ArchivePermissionError):
        history.apply(history.message(102))
    assert history.conn.execute("SELECT active FROM coach_group_memberships").fetchone() == (False,)


def test_imported_original_before_known_join_accepts_real_later_edit_but_not_unknown_old_source(history):
    registry = CoachScopeRepository(history.conn, bot_user_id=BOT)
    joined_at = history.at+timedelta(days=1)
    registry.apply_membership(CoachMembership(CHAT, 4000, joined_at, True, False))
    history.apply()
    assert history.query()[0].occurred_at < joined_at
    result = history.live(revision_at=history.now-timedelta(minutes=1), text="Correction after bot joined")
    assert result.archived and not result.queued
    assert history.query()[0].available_text == "Correction after bot joined"
    assert not history.live(101, revision_at=history.now-timedelta(minutes=1), update_id=5002).archived
    assert [m.telegram_message_id for m in history.query()] == [100]
    history.forget()
    assert not history.live(revision_at=history.now, update_id=5003).archived
    assert history.query() == []


def test_tghist_pagination_and_selector_preserve_author_topic_and_external_boundaries(history):
    history.apply(*(history.message(i, text=f"Synthetic source {i}") for i in (100, 101, 102)),
                  history.message(103, author=AUTHOR+1, text="Other author"),
                  history.message(104, topic=TOPIC+1, text="Other topic"),
                  history.message(105, forwarded_from="Synthetic outside author", text="External quote"))
    first = history.query(limit=2)
    second = history.query(limit=2, after=(first[-1].occurred_at, first[-1].source_id))
    assert [m.telegram_message_id for m in first+second] == [100, 101, 102, 105]
    assert all(m.source_origin == "historical_import" for m in first+second)
    assert len(history.query(author=AUTHOR+1)) == len(history.query(topic=TOPIC+1)) == 1
    selector = CoachSourceSelector(history.archive)
    selected = selector.select(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                               since=history.at-timedelta(days=1), until=history.now, limit=10)
    assert {m.telegram_message_id for m in selected.sources} == {100, 101, 102}
    assert selected.total_sources == 4 and selected.eligible_sources == 3
    assert selected.disclosure()["selected_imported_snapshots"] == 3
    assert not selected.disclosure()["complete_telegram_history"]
    current = selector.current(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                               source_id=first[0].source_id)
    assert current.source_origin == "historical_import" and current.telegram_update_id is None


def test_new_imported_revision_invalidates_prepared_output_and_old_proof(history):
    history.apply()
    source = history.query()[0]
    proof = SourceProof(source.source_id, source.checksum, source.access_version)
    result = history.live(200, text="@history_synthetic_bot explain an old source",
                          occurred_at=history.now-timedelta(seconds=10))
    assert result.queued
    repo = CoachRepository(history.conn, chat_id=CHAT, message_thread_id=TOPIC,
                           bot_user_id=BOT, allow_group_messages=True)
    job = repo.claim_job()
    assert job
    output_id = repo.enqueue_output(job, "Synthetic answer based on old history", source_proofs=[proof])
    assert output_id
    history.apply(history.message(text="Corrected imported history", revision_at=history.at+timedelta(minutes=1)))
    assert history.conn.execute("SELECT status,text,source_proofs FROM coaching_outputs WHERE id=%s",
                                (output_id,)).fetchone() == ("cancelled", None, [])
    assert not history.archive.reauthorize([source], chat_id=CHAT, message_thread_id=TOPIC,
                                          author_user_id=AUTHOR, actor_user_id=AUTHOR)


def test_concurrent_import_waits_for_forget_and_cannot_restore_an_unseen_old_message(history):
    history.apply()
    payload = parse_telegram_desktop_export(history.payload(history.message(101)))
    backend = Queue()

    def import_in_parallel():
        with history.pg.connect(history.url, autocommit=True,
                                options="-c lock_timeout=5000 -c statement_timeout=10000") as conn:
            backend.put(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            return SourceHistoryImportRepository(conn, bot_user_id=BOT).import_export(payload, now=history.now)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with history.conn.transaction():
            history.conn.execute("SELECT version FROM source_archive_consents WHERE chat_id=%s "
                                 "AND message_thread_id=%s AND author_user_id=%s FOR UPDATE",
                                 (CHAT, TOPIC, AUTHOR))
            future = pool.submit(import_in_parallel)
            pid = backend.get(timeout=3)
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                row = history.conn.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
                if row and row[0] == "Lock":
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Import did not reach the shared consent lock")
            history.forget()
        result = future.result(timeout=5)
    assert result["archived"] == 0 and result["permission_skipped"] == 1
    assert history.query() == []
    assert history.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0
