"""A synthetic complete pilot using real migrations/queues/ledger and fake I/O.

No Telegram group, user conversation or paid provider is contacted. These tests
establish plumbing and authorization, not the usefulness of a language model.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app_v2.coach_admin import CoachPreparationError, assert_dedicated_database, prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_main import create_app
from app_v2.coach_worker import build_worker
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ai import ProviderResponse
from app_v2.services.coach_ingestor import ingest_coach_update


BOT = 909090001
CHAT = -999000001
AUTHOR = 999000002
TOPIC = 17


@pytest.fixture
def dedicated_database():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pg = pytest.importorskip("psycopg")
    name = "coach_synthetic_" + uuid4().hex
    with pg.connect(url, autocommit=True) as admin:
        admin.execute(pg.sql.SQL("CREATE DATABASE {}").format(pg.sql.Identifier(name)))
        scoped = pg.conninfo.make_conninfo(url, dbname=name)
        try:
            yield pg, scoped
        finally:
            admin.execute(pg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(pg.sql.Identifier(name)))


class SyntheticProvider:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.silent = False

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("synthetic provider outage")
        context = json.loads(kwargs["messages"][-1]["content"])
        sources = context["sources"]
        source = next((s for s in sources if s["text"].startswith("Отчёт:")), sources[0])
        fact = {"kind": "action", "quote": source["text"][:100],
                "source_id": source["source_id"], "checksum": source["checksum"]}
        result = {"facts": [fact], "hypotheses": [], "risk_flags": [],
                  "first_time_claim": False, "recurring_claim": False}
        if context["phase"] in {"assessment", "index"}:
            result.update(search_terms=[])
        else:
            if context["mode"] in {"track", "weekly_review"}:
                text = "Персональный трек: " + source["text"]
            elif source.get("is_current_only"):
                text = "Разберём только текущий запрос. Какой результат ты заметил?"
            else:
                text = "Какой результат ты заметил?"
            refs = [{"source_id": source["source_id"], "checksum": source["checksum"]}]
            result.update(action="SILENCE" if self.silent else "RESPOND",
                          messages=[] if self.silent else [{"text": text, "source_refs": refs}],
                          experiments=[], gaps=[])
        return ProviderResponse(json.dumps(result, ensure_ascii=False), 100, 80, "synthetic-model")


class RecordingSender:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, output):
        self.sent.append(output)
        if self.fail:
            raise TimeoutError("synthetic ambiguous send")
        return {"message_id": 5000 + len(self.sent), "chat": {"id": output.chat_id},
                "message_thread_id": output.message_thread_id}


@pytest.fixture
def pilot(dedicated_database, monkeypatch):
    pg, url = dedicated_database
    config = CoachConfig(
        environment="test", enabled=True, ai_enabled=True, database_url=url,
        telegram_bot_token=f"{BOT}:synthetic_no_live_credentials", webhook_secret="s" * 40,
        openai_api_key="synthetic-no-live-key", bot_user_id=BOT, bot_username="synthetic_bot",
        chat_id=CHAT, topic_ids=(TOPIC,), archive_policy="until_author_deletes",
        ai_model="synthetic-model", daily_cap_microusd=10_000_000,
        input_price_microusd_per_million=1_000_000, output_price_microusd_per_million=2_000_000,
        max_input_tokens=500_000, max_output_tokens=2000,
    )
    assert prepare(config)["participant_grants_created"] == 0
    at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
    class SyntheticClock:
        @staticmethod
        def now(tz=None):
            return at + timedelta(hours=1)
    monkeypatch.setattr("app_v2.coach_worker.datetime", SyntheticClock)
    with pg.connect(url, autocommit=True) as conn:
        repo = CoachRepository(conn, chat_id=CHAT, message_thread_id=TOPIC, bot_user_id=BOT)
        provider, sender = SyntheticProvider(), RecordingSender()
        worker = build_worker(conn, config, TOPIC, sender=sender, provider=provider)
        counter = 0

        def update(text, *, author=AUTHOR, topic=TOPIC, edit=False, message_id=None, **extras):
            nonlocal counter
            counter += 1
            message = {"message_id": message_id or counter, "date": int((at + timedelta(seconds=counter)).timestamp()),
                       "from": {"id": author, "is_bot": False},
                       "chat": {"id": CHAT, "type": "supergroup", "is_forum": True},
                       "message_thread_id": topic, "is_topic_message": True, "text": text, **extras}
            if edit:
                message["edit_date"] = message["date"]
            return {"update_id": counter, "edited_message" if edit else "message": message}

        def drain():
            for _ in range(40):
                if not worker.run_once():
                    return
            pytest.fail("synthetic queue did not drain")

        def intake(text, *, process=True, **kwargs):
            event = update(text, **kwargs)
            result = ingest_coach_update(event, conn=conn, config=config)
            if process:
                drain()
            return event, result

        def grants(*commands):
            intake("/privacy")
            for command in commands:
                intake(command)

        yield SimpleNamespace(pg=pg, url=url, config=config, conn=conn, repo=repo,
                              provider=provider, sender=sender, worker=worker, update=update,
                              drain=drain, intake=intake, grants=grants, at=at)


def test_complete_group_default_archive_auto_direct_track_and_deletion(pilot):
    report, result = pilot.intake("Отчёт: проверил договорённость коротким звонком.")
    assert result.archived and result.queued
    assert pilot.repo.permissions(AUTHOR).automatic_allowed
    assert pilot.repo.permissions(AUTHOR).track_status == "active"
    assert pilot.conn.execute("SELECT grant_basis,actor_user_id FROM source_archive_consents").fetchone() == ("group_default", None)
    assert "Какой результат" in pilot.sender.sent[-1].text
    assert pilot.sender.sent[-1].source_proofs
    assert len(pilot.provider.calls) == 2
    assert pilot.conn.execute("SELECT count(*) FROM coach_ai_operations").fetchone()[0] == 2
    assert pilot.repo.list_episodes(author_user_id=AUTHOR)

    _, result = pilot.intake("/ask помоги разобрать мой следующий шаг")
    assert result.queued and pilot.sender.sent[-1].mode == "direct"
    before = len(pilot.provider.calls)
    pilot.intake("Напарник, покажи мой трек за 30 дней")
    assert len(pilot.provider.calls) == before + 1
    assert pilot.sender.sent[-1].mode == "track"
    track_text = "\n".join(item.text for item in pilot.sender.sent if item.mode == "track")
    assert "Персональный трек" in track_text
    assert "договорённость" in track_text

    pilot.intake("Напарник, забудь мои сообщения")
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0
    assert pilot.repo.list_episodes(author_user_id=AUTHOR) == []
    assert pilot.repo.permissions(AUTHOR).track_status == "off"
    assert not pilot.repo.permissions(AUTHOR).retain_source
    assert pilot.conn.execute("SELECT count(*) FROM coach_dialogue_turns WHERE text IS NOT NULL").fetchone()[0] == 0
    for table in ("events", "messages", "outbox", "users", "chats"):
        assert pilot.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_direct_only_leaves_no_archive_or_durable_episodes(pilot):
    pilot.intake("Напарник, не запоминай мои сообщения")
    _, result = pilot.intake("/ask у меня один вопрос о следующем шаге")
    assert result.queued and not result.archived
    assert pilot.sender.sent[-1].mode == "direct"
    assert "только текущий запрос" in pilot.sender.sent[-1].text
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0
    assert pilot.conn.execute("SELECT count(*) FROM coach_episodes").fetchone()[0] == 0
    assert pilot.conn.execute("SELECT count(*) FROM coach_jobs WHERE current_text IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize("outage", ["disabled", "provider", "budget"])
def test_archive_survives_ai_stop_and_direct_request_gets_honest_notice(pilot, outage):
    pilot.grants("/archive_on", "/history_on", "/direct_on", "/auto_on")
    if outage == "disabled":
        pilot.worker.ai_enabled = False
    elif outage == "provider":
        pilot.provider.fail = True
    else:
        pilot.worker.engine.ai.config = replace(pilot.worker.engine.ai.config, daily_cap_microusd=1)
    _, result = pilot.intake("Отчёт: источник должен сохраниться независимо от AI.")
    assert result.archived
    _, result = pilot.intake("/ask объясни мой отчёт")
    assert result.archived and result.queued
    assert "Сейчас разбор недоступен" in pilot.sender.sent[-1].text
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text LIKE 'Отчёт:%'").fetchone()[0] == 1
    if outage in {"disabled", "budget"}:
        assert pilot.provider.calls == []


def test_silence_still_archives_and_tracks_without_public_message(pilot):
    pilot.grants("/archive_on", "/history_on", "/auto_on", "/track_start")
    pilot.provider.silent = True
    sent = len(pilot.sender.sent)
    _, result = pilot.intake("Отчёт: обычный выполненный пункт.")
    assert result.archived and result.queued
    assert len(pilot.provider.calls) == 2 and len(pilot.sender.sent) == sent
    assert pilot.repo.list_episodes(author_user_id=AUTHOR)
    pilot.intake("/auto_off")
    sent = len(pilot.sender.sent)
    _, result = pilot.intake("Отчёт: ещё одно действие для моего трека.")
    assert result.archived and result.queued
    assert len(pilot.sender.sent) == sent
    assert pilot.conn.execute("SELECT mode FROM coach_jobs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "index"


def test_revoke_after_generation_cancels_prepared_historical_reply(pilot):
    pilot.grants("/archive_on", "/history_on", "/direct_on", "/auto_on")
    pilot.intake("Отчёт: не должен попасть в ответ после отзыва.", process=False)
    assert pilot.worker.run_once()  # generation only, outbox is durable
    assert pilot.conn.execute("SELECT count(*) FROM coaching_outputs WHERE status='pending' AND mode='auto_report'").fetchone()[0] == 1
    sent = len(pilot.sender.sent)
    pilot.intake("/history_off")
    assert all(output.mode == "control" for output in pilot.sender.sent[sent:])


def test_ambiguous_send_has_no_blind_retry(pilot):
    pilot.grants("/direct_on")
    pilot.intake("/ask вопрос для проверки неоднозначной отправки", process=False)
    assert pilot.worker.run_once()
    pilot.sender.fail = True
    before = len(pilot.sender.sent)
    assert pilot.worker.run_once()
    pilot.sender.fail = False
    pilot.drain()
    assert len(pilot.sender.sent) == before + 1
    assert pilot.conn.execute("SELECT status,text FROM coaching_outputs WHERE mode='direct'").fetchone() == ("uncertain", None)


def test_unconfigured_scopes_and_unconsented_nested_text_are_never_stored(pilot):
    pilot.intake("Напарник, не запоминай мои сообщения")
    _, wrong = pilot.intake("PRIVATE_OTHER_TOPIC", topic=18)
    assert wrong.status == "ignored"
    pilot.intake("/ask мой собственный короткий вопрос", reply_to_message={
        "message_id": 200, "from": {"id": AUTHOR + 1, "is_bot": False}, "text": "PRIVATE_OTHER_AUTHOR",
    })
    assert pilot.provider.calls
    assert all("PRIVATE_OTHER" not in json.dumps(call, ensure_ascii=False) for call in pilot.provider.calls)
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0


def test_webhook_ack_follows_atomic_archive_and_queue_commit(pilot, monkeypatch):
    pilot.grants("/archive_on", "/history_on", "/auto_on")
    event = pilot.update("Отчёт: транзакция webhook.")
    factory = lambda: pilot.pg.connect(pilot.url, autocommit=True)
    app = create_app(pilot.config, connection_factory=factory)
    original = CoachRepository.enqueue_job
    def crash_after_archive(*args, **kwargs):
        raise RuntimeError("synthetic write failure")
    monkeypatch.setattr(CoachRepository, "enqueue_job", crash_after_archive)
    with TestClient(app) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": pilot.config.webhook_secret}
        assert client.post("/webhooks/telegram", json=event, headers=headers).status_code == 503
        assert pilot.conn.execute("SELECT 1 FROM coach_ingest_receipts WHERE telegram_update_id=%s", (event["update_id"],)).fetchone() is None
        assert pilot.conn.execute("SELECT 1 FROM source_archive_revisions WHERE telegram_update_id=%s", (event["update_id"],)).fetchone() is None
        monkeypatch.setattr(CoachRepository, "enqueue_job", original)
        assert client.post("/webhooks/telegram", json=event, headers=headers).status_code == 200
        assert client.post("/webhooks/telegram", json=event, headers=headers).json()["status"] == "duplicate"
        assert pilot.conn.execute("SELECT count(*) FROM coach_jobs WHERE telegram_update_id=%s", (event["update_id"],)).fetchone()[0] == 1
        assert client.get("/ready").status_code == 200


def test_prepare_is_idempotent_and_refuses_another_bot(pilot):
    before = pilot.conn.execute("SELECT activated_at FROM source_archive_scopes").fetchone()[0]
    assert prepare(pilot.config)["migrations_applied"] == []
    assert pilot.conn.execute("SELECT activated_at FROM source_archive_scopes").fetchone()[0] == before
    with pytest.raises(CoachPreparationError, match="another_bot"):
        prepare(replace(pilot.config, bot_user_id=BOT + 1))
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_consents").fetchone()[0] == 0


def test_prepare_rejects_unrelated_database_before_any_migration(dedicated_database):
    pg, url = dedicated_database
    with pg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE unrelated_accounting (amount numeric)")
        with pytest.raises(CoachPreparationError, match="unrelated_application"):
            assert_dedicated_database(conn, BOT)
        assert conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None


def test_prepare_rejects_bound_database_with_legacy_messages(pilot):
    # A bound identity does not bypass the guard if another application schema
    # starts carrying data. Use a minimal legacy chat row, not production data.
    pilot.conn.execute("INSERT INTO chats(telegram_chat_id,chat_type) VALUES(-999000099,'group')")
    with pytest.raises(CoachPreparationError, match="existing_bot_data"):
        assert_dedicated_database(pilot.conn, BOT)


def test_disabled_scope_stops_jobs_but_accepts_revocation_controls(pilot):
    pilot.grants("/archive_on", "/history_on", "/direct_on", "/auto_on")
    event, _ = pilot.intake("Отчёт: ожидающее задание.", process=False)
    SourceArchiveRepository(pilot.conn).configure_scope(CHAT, TOPIC, enabled=False)
    pilot.drain()  # cancellation must not raise and stop other topics' workers
    assert pilot.conn.execute("SELECT status FROM coach_jobs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "cancelled"
    assert pilot.provider.calls == []
    _, result = pilot.intake("Отчёт: тема выключена.")
    assert result.status == "disabled" and not result.queued
    _, result = pilot.intake("Поправка после отключения.", edit=True, message_id=event["message"]["message_id"])
    assert result.status == "disabled"
    pilot.intake("/forget")
    assert "Удалил твои источники" in pilot.sender.sent[-1].text
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0


def test_direct_only_edit_before_original_never_reaches_ai(pilot):
    pilot.intake("Напарник, не запоминай мои сообщения")
    original = pilot.update("/ask устаревший вариант запроса")
    edit = pilot.update("исправленный вариант", edit=True, message_id=original["message"]["message_id"])
    edit["edited_message"]["date"] = original["message"]["date"]
    first = ingest_coach_update(edit, conn=pilot.conn, config=pilot.config)
    later = ingest_coach_update(original, conn=pilot.conn, config=pilot.config)
    pilot.drain()
    assert not first.queued and not later.queued
    assert pilot.provider.calls == []
    assert pilot.conn.execute("SELECT count(*) FROM coach_jobs").fetchone()[0] == 0
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0
    assert pilot.conn.execute("SELECT checksum FROM coach_message_versions WHERE telegram_message_id=%s", (original["message"]["message_id"],)).fetchone() == (None,)
