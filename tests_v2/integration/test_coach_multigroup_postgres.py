"""Joined-group behavior against a fresh local PostgreSQL, with synthetic I/O."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_main import create_app
from app_v2.coach_worker import CoachWorkerPool
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ingestor import ingest_coach_update
from tests_v2.integration.test_coach_lifecycle_postgres import dedicated_database, SyntheticProvider


BOT, AUTHOR = 908080001, 908080002
GROUP_A, GROUP_B = -998080001, -998080002


class GroupSender:
    def __init__(self):
        self.sent = []

    def send(self, output):
        self.sent.append(output)
        receipt = {"message_id": 7000 + len(self.sent), "chat": {"id": output.chat_id}}
        if output.message_thread_id != 1:
            receipt.update(message_thread_id=output.message_thread_id, is_topic_message=True)
        return receipt


@pytest.fixture
def joined(dedicated_database, monkeypatch):
    pg, url = dedicated_database
    config = CoachConfig(
        environment="test", enabled=True, ai_enabled=True, group_mode="joined_groups",
        database_url=url, telegram_bot_token=f"{BOT}:synthetic_not_a_live_token",
        webhook_secret="synthetic_group_secret_" + "s" * 32, openai_api_key="synthetic",
        bot_user_id=BOT, bot_username="synthetic_group_bot", ai_model="synthetic-model",
        daily_cap_microusd=10_000_000, input_price_microusd_per_million=1_000_000,
        output_price_microusd_per_million=2_000_000,
        max_input_tokens=500_000, max_output_tokens=2000,
    )
    prepared = prepare(config)
    at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)

    class EngineClock:
        @staticmethod
        def now(tz=None):
            return at + timedelta(hours=1)

    monkeypatch.setattr("app_v2.coach_worker.datetime", EngineClock)
    with pg.connect(url, autocommit=True) as conn:
        provider, sender = SyntheticProvider(), GroupSender()
        pool = CoachWorkerPool(conn, config, sender=sender, provider=provider)
        counter = 0

        def next_id(value=None):
            nonlocal counter
            counter = max(counter + 1, value or 0)
            return counter if value is None else value

        def event(text, *, chat=GROUP_A, topic=17, kind="supergroup", forum=True,
                  update_id=None, message_id=None, occurred_at=None, ordinary_thread=None):
            identifier = next_id(update_id)
            message = {
                "message_id": identifier if message_id is None else message_id,
                "date": int((occurred_at or at + timedelta(seconds=identifier)).timestamp()),
                "from": {"id": AUTHOR, "is_bot": False},
                "chat": {"id": chat, "type": kind, "is_forum": forum}, "text": text,
            }
            if topic not in {None, 1}:
                message.update(message_thread_id=topic, is_topic_message=True)
            elif ordinary_thread is not None:
                message["message_thread_id"] = ordinary_thread
            return {"update_id": identifier, "message": message}

        def member(*, chat=GROUP_A, old="member", new="left", occurred_at=None, update_id=None):
            identifier = next_id(update_id)
            user = {"id": BOT, "is_bot": True}
            return {"update_id": identifier, "my_chat_member": {
                "chat": {"id": chat, "type": "supergroup", "is_forum": True},
                "from": {"id": AUTHOR, "is_bot": False},
                "date": int((occurred_at or at + timedelta(seconds=identifier)).timestamp()),
                "old_chat_member": {"status": old, "user": user},
                "new_chat_member": {"status": new, "user": user},
            }}

        def ingest(update):
            return ingest_coach_update(update, conn=conn, config=config)

        def drain(target=None):
            for _ in range(50):
                if not (target or pool).run_once():
                    return
            pytest.fail("Joined-group synthetic queue did not drain")

        def intake(text, *, process=True, **fields):
            update = event(text, **fields)
            result = ingest(update)
            if process:
                drain()
            return update, result

        def repo(chat=GROUP_A, topic=17):
            return CoachRepository(conn, chat_id=chat, message_thread_id=topic,
                                   bot_user_id=BOT, allow_group_messages=True)

        yield SimpleNamespace(pg=pg, url=url, conn=conn, config=config, prepared=prepared,
                              at=at, event=event, member=member, ingest=ingest, intake=intake,
                              drain=drain, repo=repo, pool=pool, provider=provider, sender=sender)


def test_prepare_and_real_readiness_work_before_first_joined_group(joined):
    assert joined.config.chat_id is None and joined.config.topic_ids == ()
    assert joined.prepared["participant_grants_created"] == 0
    assert joined.conn.execute("SELECT count(*) FROM source_archive_scopes").fetchone()[0] == 0
    assert joined.conn.execute("SELECT count(*) FROM coach_group_memberships").fetchone()[0] == 0
    client = TestClient(create_app(joined.config, connection_factory=lambda: joined.pg.connect(
        joined.url, autocommit=True)))
    assert client.get("/ready").status_code == 200
    assert not joined.pool.run_once() and joined.pool.workers == {}
    assert joined.provider.calls == [] and joined.sender.sent == []


@pytest.mark.parametrize("fields,expected_topic", [
    ({"topic": 1}, 1),
    ({"topic": 1, "ordinary_thread": 93}, 1),
    ({"kind": "group", "forum": False, "topic": 1}, 1),
    ({"forum": False, "topic": 1, "ordinary_thread": 93}, 1),
    ({"topic": 17}, 17),
])
def test_first_received_message_is_archived_analyzed_and_delivered_in_exact_scope(joined, fields, expected_topic):
    _, result = joined.intake("Отчёт: первая синтетическая запись группы.", **fields)
    assert result.archived and result.queued
    assert joined.conn.execute(
        "SELECT chat_id,message_thread_id,available_text FROM source_archive_revisions"
    ).fetchone() == (GROUP_A, expected_topic, "Отчёт: первая синтетическая запись группы.")
    assert len(joined.provider.calls) == 2
    assert len(joined.sender.sent) == 1
    assert (joined.sender.sent[0].chat_id, joined.sender.sent[0].message_thread_id) == (GROUP_A, expected_topic)
    assert joined.conn.execute("SELECT count(*) FROM coach_dialogue_turns WHERE text IS NOT NULL").fetchone()[0] == 1
    assert joined.repo(topic=expected_topic).permissions(AUTHOR).automatic_allowed


def test_two_groups_with_same_author_topic_and_message_id_never_mix_history_or_outputs(joined):
    joined.intake("Отчёт: GROUP_ALPHA_ONLY выполнил действие.", chat=GROUP_A, message_id=1)
    calls_after_a = len(joined.provider.calls)
    joined.intake("Отчёт: GROUP_BETA_ONLY сделал другой шаг.", chat=GROUP_B, message_id=1)
    rows = joined.conn.execute(
        "SELECT chat_id,message_thread_id,author_user_id,telegram_message_id FROM source_archive_messages ORDER BY chat_id"
    ).fetchall()
    assert rows == [(GROUP_B, 17, AUTHOR, 1), (GROUP_A, 17, AUTHOR, 1)]
    assert len(joined.provider.calls) == calls_after_a + 2
    for call in joined.provider.calls[:calls_after_a]:
        payload = json.dumps(call["messages"], ensure_ascii=False)
        assert "GROUP_ALPHA_ONLY" in payload and "GROUP_BETA_ONLY" not in payload
    for call in joined.provider.calls[calls_after_a:]:
        payload = json.dumps(call["messages"], ensure_ascii=False)
        assert "GROUP_BETA_ONLY" in payload and "GROUP_ALPHA_ONLY" not in payload
    assert {output.chat_id for output in joined.sender.sent} == {GROUP_A, GROUP_B}
    for output in joined.sender.sent:
        assert output.message_thread_id == 17 and output.author_user_id == AUTHOR
        assert all(proof.source_id.startswith(f"tgsrc:{output.chat_id}:17:") for proof in output.source_proofs)


def test_same_worker_pool_discovers_new_group_and_topic_without_restart(joined):
    joined.intake("Отчёт: первая тема.", topic=17)
    first_worker = joined.pool.workers[(GROUP_A, 17)]
    joined.intake("Отчёт: новая тема.", topic=29)
    joined.intake("Отчёт: новая группа.", chat=GROUP_B, topic=1)
    assert joined.pool.workers[(GROUP_A, 17)] is first_worker
    assert set(joined.pool.workers) == {(GROUP_A, 17), (GROUP_A, 29), (GROUP_B, 1)}
    assert {(output.chat_id, output.message_thread_id) for output in joined.sender.sent} == set(joined.pool.workers)


def test_out_of_order_initial_messages_are_not_lost_by_dynamic_scope_cutoff(joined):
    later = joined.event("Отчёт: позже написанный источник.", update_id=100, message_id=2,
                         occurred_at=joined.at + timedelta(seconds=20))
    earlier = joined.event("Отчёт: раньше написанный источник.", update_id=90, message_id=1,
                           occurred_at=joined.at + timedelta(seconds=10))
    assert joined.ingest(later).archived
    assert joined.ingest(earlier).archived
    assert joined.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone()[0] == 2
    assert joined.ingest(earlier).status == "duplicate"
    assert joined.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone()[0] == 2


def test_a_received_message_never_reenables_an_operator_disabled_topic(joined):
    joined.intake("Отчёт: до отключения темы.")
    SourceArchiveRepository(joined.conn).configure_scope(GROUP_A, 17, enabled=False)
    before_calls, before_sent = len(joined.provider.calls), len(joined.sender.sent)
    _, result = joined.intake("Отчёт: выключенная тема должна остаться выключенной.")
    assert result.status == "disabled" and not result.archived and not result.queued
    assert len(joined.provider.calls) == before_calls and len(joined.sender.sent) == before_sent
    assert joined.conn.execute(
        "SELECT enabled FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=17", (GROUP_A,)
    ).fetchone() == (False,)


@pytest.mark.parametrize("control,retains", [
    ("Арни, пока не комментируй", True), ("Арни, забудь мои сообщения", False),
])
def test_personal_stop_or_forget_survives_bot_removal_and_rejoin(joined, control, retains):
    joined.intake("Отчёт: исходный личный источник.")
    joined.intake(control)
    permission = joined.repo().permissions(AUTHOR)
    assert not permission.unsolicited_group_reply and permission.retain_source is retains
    joined.ingest(joined.member(old="member", new="left", occurred_at=joined.at + timedelta(seconds=30)))
    joined.ingest(joined.member(old="left", new="member", occurred_at=joined.at + timedelta(seconds=40)))
    before_calls = len(joined.provider.calls)
    _, result = joined.intake("Отчёт: после повторного добавления.", occurred_at=joined.at + timedelta(seconds=41))
    assert result.archived is retains and not result.queued
    assert len(joined.provider.calls) == before_calls
    after = joined.repo().permissions(AUTHOR)
    assert after.version == permission.version
    assert not after.unsolicited_group_reply and after.retain_source is retains
    if not retains:
        assert joined.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0] == 0


def test_monetary_guard_is_one_shared_budget_before_ai_for_all_groups(joined):
    bounded = replace(joined.config, daily_cap_microusd=joined.config.ai_config().max_cost_microusd)
    reservations_seen = []
    original = joined.provider.complete_json

    def guarded_provider(**arguments):
        with joined.pg.connect(joined.url, autocommit=True) as observer:
            reserved = observer.execute("SELECT reserved_microusd FROM coach_budget_days").fetchone()[0]
            assert reserved > 0
            reservations_seen.append(reserved)
        return original(**arguments)

    joined.provider.complete_json = guarded_provider
    pool = CoachWorkerPool(joined.conn, bounded, sender=joined.sender, provider=joined.provider)
    joined.intake("Отчёт: первая группа расходует общий резерв.", chat=GROUP_A, process=False)
    joined.drain(pool)
    assert len(joined.provider.calls) == 1
    joined.intake("Отчёт: вторая группа не получает отдельный бюджет.", chat=GROUP_B, process=False)
    joined.drain(pool)
    assert len(joined.provider.calls) == 1 and len(reservations_seen) == 1
    assert joined.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone()[0] == 2
    assert joined.conn.execute("SELECT count(*) FROM coach_budget_days").fetchone()[0] == 1
    assert joined.conn.execute("SELECT count(*) FROM coach_ai_operations").fetchone()[0] == 1
    assert joined.conn.execute("SELECT spent_microusd,reserved_microusd FROM coach_budget_days").fetchone() == (260, 0)


@pytest.mark.parametrize("same_second", [False, True])
def test_rejoin_arriving_before_old_leave_cancels_old_queue_and_does_not_replay(joined, same_second):
    moment = joined.at + timedelta(seconds=30)
    joined.ingest(joined.member(old="left", new="member", update_id=10, occurred_at=joined.at))
    joined.intake("Отчёт: старое задание не переживает новое присоединение.", process=False,
                  update_id=20, occurred_at=moment if same_second else moment - timedelta(seconds=1))
    assert joined.conn.execute("SELECT count(*) FROM coach_jobs WHERE status='pending'").fetchone()[0] == 1
    # The later join reaches this worker before its corresponding earlier leave.
    joined.ingest(joined.member(old="left", new="member", update_id=40, occurred_at=moment))
    joined.ingest(joined.member(old="member", new="left", update_id=30,
                               occurred_at=moment if same_second else moment - timedelta(seconds=1)))
    joined.drain()
    assert joined.provider.calls == [] and joined.sender.sent == []
    assert joined.conn.execute("SELECT count(*) FROM coach_jobs WHERE status='cancelled'").fetchone()[0] == 1
    _, fresh = joined.intake("Отчёт: новое сообщение после присоединения.", update_id=50,
                             occurred_at=moment + timedelta(seconds=1))
    assert fresh.archived and fresh.queued
    assert len(joined.sender.sent) == 1
