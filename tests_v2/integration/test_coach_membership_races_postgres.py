"""Real PostgreSQL membership boundaries with synthetic messages and transport.

The final-send race uses independent sender/removal connections and observes an
actual database lock wait. Every barrier and query has a bounded timeout.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic

import pytest

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.coach_worker import CoachWorkerPool
from app_v2.domain.coach import SourceProof
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ingestor import ingest_coach_update
from tests_v2.integration.test_coach_lifecycle_postgres import (
    AUTHOR, BOT, CHAT, TOPIC, dedicated_database, pilot,
)


def joined(pilot):
    return replace(pilot.config, group_mode="joined_groups", chat_id=None, topic_ids=())


def membership(pilot, *, update_id=1000, seconds=20, old="member", new="left", chat=CHAT):
    return {"update_id": update_id, "my_chat_member": {
        "chat": {"id": chat, "type": "supergroup", "is_forum": True},
        "date": int((pilot.at + timedelta(seconds=seconds)).timestamp()),
        "old_chat_member": {"status": old, "user": {"id": BOT, "is_bot": True}},
        "new_chat_member": {"status": new, "user": {"id": BOT, "is_bot": True}},
    }}


def intake(pilot, event, *, conn=None):
    return ingest_coach_update(event, conn=pilot.conn if conn is None else conn, config=joined(pilot))


@pytest.mark.parametrize("invalid", ["new_bot", "old_bot", "human", "private", "date_bool", "restricted"])
def test_invalid_membership_does_not_mutate_active_registry(pilot, invalid):
    assert intake(pilot, membership(pilot, old="left", new="member", seconds=0)).status == "membership_updated"
    before = pilot.conn.execute(
        "SELECT active,active_since,changed_at,membership_update_id FROM coach_group_memberships WHERE chat_id=%s",
        (CHAT,),
    ).fetchone()
    event = membership(pilot, update_id=1001)
    body = event["my_chat_member"]
    if invalid == "new_bot":
        body["new_chat_member"]["user"]["id"] += 1
    elif invalid == "old_bot":
        body["old_chat_member"]["user"]["id"] += 1
    elif invalid == "human":
        body["new_chat_member"]["user"]["is_bot"] = False
    elif invalid == "private":
        body["chat"]["type"] = "private"
    elif invalid == "date_bool":
        body["date"] = True
    else:
        body["new_chat_member"]["status"] = "restricted"  # missing is_member
    assert intake(pilot, event).status == "ignored"
    assert pilot.conn.execute(
        "SELECT active,active_since,changed_at,membership_update_id FROM coach_group_memberships WHERE chat_id=%s",
        (CHAT,),
    ).fetchone() == before


def test_late_and_duplicate_joins_do_not_reactivate_removed_group(pilot):
    assert intake(pilot, membership(pilot, old="left", new="member", seconds=0)).status == "membership_updated"
    removal = membership(pilot, update_id=1200, seconds=20)
    assert intake(pilot, removal).status == "membership_updated"
    for event in (
        membership(pilot, old="left", new="member", update_id=9999, seconds=19),
        membership(pilot, old="left", new="member", update_id=1199, seconds=20),
        removal,
    ):
        assert intake(pilot, event).status == "duplicate"
    assert pilot.conn.execute("SELECT active FROM coach_group_memberships WHERE chat_id=%s", (CHAT,)).fetchone() == (False,)
    assert intake(pilot, pilot.update("Отчёт: запоздавшее сообщение после удаления бота.")).status == "inactive_group"
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone() == (0,)


@pytest.mark.parametrize("message_before_promotion", [False, True])
def test_first_promotion_preserves_received_message_cutoff(pilot, message_before_promotion):
    if message_before_promotion:
        assert intake(pilot, pilot.update("Отчёт: первое сообщение до повышения.")).archived
    promotion = membership(pilot, old="member", new="administrator", seconds=100)
    assert intake(pilot, promotion).status == "membership_updated"
    assert pilot.conn.execute(
        "SELECT active_since FROM coach_group_memberships WHERE chat_id=%s", (CHAT,),
    ).fetchone() == (datetime(1970, 1, 1, tzinfo=timezone.utc),)
    # This message reaches the webhook after promotion, but was sent before it.
    delayed = pilot.update("Отчёт: доставлено после повышения, написано раньше.")
    assert delayed["message"]["date"] < promotion["my_chat_member"]["date"]
    assert intake(pilot, delayed).archived
    assert pilot.conn.execute("SELECT count(*) FROM source_archive_revisions").fetchone() == (
        2 if message_before_promotion else 1,
    )


def test_final_send_serializes_with_removal_and_cancels_control_and_bundle_tail(pilot):
    assert intake(pilot, pilot.update("Арни, разберём синтетический следующий шаг.")).queued
    job = pilot.repo.claim_job()
    proof = SourceProof(job.source_id, job.source_checksum, job.consent_version)
    reply_ids = pilot.repo.enqueue_outputs(job, ("Первая часть", "Хвост ответа"), source_proofs=(proof,))
    assert len(reply_ids) == 2
    pilot.repo.complete_job(job)
    control_ids = []
    for _ in range(2):
        normalized = normalize_update(pilot.update("/privacy"), bot_username=pilot.config.bot_username,
                                      bot_user_id=str(BOT))
        control_ids.append(pilot.repo.enqueue_control_output(normalized, "Синтетическая служебная квитанция"))
    first = pilot.repo.claim_output()
    assert first.id == reply_ids[0]
    claimed_control = pilot.repo.claim_output()
    assert claimed_control.id == control_ids[0] and claimed_control.mode == "control"

    entered, release, removing_started = Event(), Event(), Event()
    sent, removal_pid = [], []
    options = "-c statement_timeout=4000 -c lock_timeout=4000"

    def send_first():
        with pilot.pg.connect(pilot.url, autocommit=True, options=options) as conn:
            repo = CoachRepository(conn, chat_id=CHAT, message_thread_id=TOPIC,
                                   bot_user_id=BOT, allow_group_messages=True)
            def network(output):
                sent.append(output.id)
                entered.set()
                assert release.wait(5), "send boundary was not released"
                return {"message_id": 8001, "chat": {"id": CHAT},
                        "message_thread_id": TOPIC, "is_topic_message": True}
            return repo.deliver_output(first, network)

    def remove():
        with pilot.pg.connect(pilot.url, autocommit=True, options=options) as conn:
            removal_pid.append(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            removing_started.set()
            return intake(pilot, membership(pilot), conn=conn)

    with ThreadPoolExecutor(max_workers=2) as threads:
        sending = threads.submit(send_first)
        removing = None
        try:
            assert entered.wait(5), "sender did not reach the final guarded send"
            removing = threads.submit(remove)
            assert removing_started.wait(5), "removal did not start"
            deadline, lock_observed = monotonic() + 3, False
            while monotonic() < deadline:
                state = pilot.conn.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (removal_pid[0],),
                ).fetchone()
                if state == ("Lock",):
                    lock_observed = True
                    break
                assert not removing.done(), "removal completed during the guarded network send"
                release.wait(0.01)
            assert lock_observed, "PostgreSQL never observed the membership lock boundary"
        finally:
            release.set()
        assert sending.result(timeout=5) == "sent"
        assert removing.result(timeout=5).status == "membership_updated"

    assert sent == [reply_ids[0]]
    states = dict(pilot.conn.execute("SELECT id,status FROM coaching_outputs").fetchall())
    assert states[reply_ids[0]] == "sent"
    assert all(states[key] == "cancelled" for key in (reply_ids[1], *control_ids))
    assert pilot.conn.execute("SELECT count(*) FROM coaching_outputs WHERE text IS NOT NULL").fetchone() == (0,)
    def forbidden_network(output):
        pytest.fail("removed or previously cancelled output reached transport")
    assert pilot.repo.deliver_output(claimed_control, forbidden_network) == "cancelled"
    assert pilot.repo.claim_output() is None
    assert intake(pilot, membership(pilot, old="left", new="member", update_id=2000, seconds=200)).status == "membership_updated"
    assert pilot.repo.deliver_output(claimed_control, forbidden_network) == "cancelled"
    assert pilot.repo.claim_output() is None


def test_removal_during_ai_cancels_its_job_and_next_group_keeps_working(pilot, monkeypatch):
    first = pilot.update("Арни, Отчёт: синтетическая первая группа.")
    other = pilot.update("Арни, Отчёт: синтетическая другая группа.")
    other["message"]["chat"]["id"] = CHAT + 1
    assert intake(pilot, first).queued
    assert intake(pilot, other).queued
    removed = Event()
    complete = pilot.provider.complete_json
    def removal_during_provider(**kwargs):
        if not removed.is_set():
            with pilot.pg.connect(pilot.url, autocommit=True,
                                  options="-c statement_timeout=4000 -c lock_timeout=4000") as conn:
                assert intake(pilot, membership(pilot), conn=conn).status == "membership_updated"
            removed.set()
        return complete(**kwargs)
    monkeypatch.setattr(pilot.provider, "complete_json", removal_during_provider)
    pool = CoachWorkerPool(pilot.conn, joined(pilot), sender=pilot.sender, provider=pilot.provider)
    for _ in range(12):
        if not pool.run_once():
            break
    else:
        pytest.fail("synthetic two-group queue did not drain")
    assert removed.is_set()
    assert pilot.sender.sent and all(output.chat_id == CHAT + 1 for output in pilot.sender.sent)
    assert pilot.conn.execute("SELECT status FROM coach_jobs WHERE chat_id=%s", (CHAT,)).fetchone() == ("cancelled",)
    assert pilot.conn.execute(
        "SELECT count(*) FROM coaching_outputs WHERE chat_id=%s AND status IN ('pending','sending','sent')", (CHAT,),
    ).fetchone() == (0,)


def dated_message(pilot, text, *, update_id, seconds, message_id=None, edited_seconds=None):
    event = pilot.update(text, message_id=message_id, edit=edited_seconds is not None)
    event["update_id"] = update_id
    message = event["edited_message" if edited_seconds is not None else "message"]
    message["date"] = int((pilot.at + timedelta(seconds=seconds)).timestamp())
    if edited_seconds is not None:
        message["edit_date"] = int((pilot.at + timedelta(seconds=edited_seconds)).timestamp())
    return event


def test_edit_of_message_sent_in_join_second_archives_revision_and_cancels_old_reply(pilot):
    join = membership(pilot, old="left", new="member", update_id=10, seconds=0)
    assert intake(pilot, join).status == "membership_updated"
    original = dated_message(pilot, "Арни, исходное сообщение в секунду подключения.", update_id=11, seconds=0)
    assert intake(pilot, original).archived
    job = pilot.repo.claim_job()
    proof = SourceProof(job.source_id, job.source_checksum, job.consent_version)
    output_id = pilot.repo.enqueue_output(job, "Ответ по старой редакции", source_proofs=(proof,))
    assert pilot.conn.execute("SELECT status FROM coaching_outputs WHERE id=%s", (output_id,)).fetchone() == ("pending",)
    edit = dated_message(pilot, "Арни, исправленная формулировка.", update_id=12, seconds=0,
                         message_id=original["message"]["message_id"], edited_seconds=5)
    result = intake(pilot, edit)
    assert result.archived and not result.queued
    revisions = pilot.conn.execute(
        "SELECT available_text FROM source_archive_revisions WHERE telegram_message_id=%s ORDER BY message_revision",
        (original["message"]["message_id"],),
    ).fetchall()
    assert revisions == [(original["message"]["text"],), (edit["edited_message"]["text"],)]
    assert pilot.conn.execute("SELECT status,text FROM coaching_outputs WHERE id=%s", (output_id,)).fetchone() == ("cancelled", None)


@pytest.mark.parametrize("edit_seconds,edit_update_id", [(2, 12), (6, 16)],
                         ids=["pre-removal-edit-delivered-after-rejoin", "post-rejoin-edit"])
def test_edit_of_known_source_after_rejoin_invalidates_new_reply_using_old_revision(
        pilot, edit_seconds, edit_update_id):
    assert intake(pilot, membership(pilot, old="left", new="member", update_id=10, seconds=0)).status == "membership_updated"
    original = dated_message(pilot, "Отчёт: ранее сохранённая исходная версия.", update_id=11, seconds=1)
    assert intake(pilot, original).archived
    archive = SourceArchiveRepository(pilot.conn)
    scope = dict(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR, actor_user_id=AUTHOR)
    previous = archive.query_sources(**scope, since=pilot.at, until=pilot.at + timedelta(hours=1))
    assert len(previous) == 1 and archive.reauthorize(previous, **scope)
    assert intake(pilot, membership(pilot, update_id=13, seconds=3)).status == "membership_updated"
    assert intake(pilot, membership(pilot, old="left", new="member", update_id=14, seconds=4)).status == "membership_updated"

    request = dated_message(pilot, "Арни, сравни мой новый шаг с прошлым отчётом.", update_id=15, seconds=5)
    assert intake(pilot, request).queued
    job = pilot.repo.claim_job()
    proofs = (SourceProof(previous[0].source_id, previous[0].checksum, previous[0].access_version),
              SourceProof(job.source_id, job.source_checksum, job.consent_version))
    output_id = pilot.repo.enqueue_output(job, "Новый ответ использовал старый отчёт", source_proofs=proofs)
    assert output_id
    # A known-source correction may predate leave/rejoin and arrive afterwards.
    # It must still supersede the stored original and invalidate dependent work.
    edit = dated_message(pilot, "Отчёт: исправленная версия, доставленная после возвращения бота.",
                         update_id=edit_update_id, seconds=1,
                         message_id=original["message"]["message_id"], edited_seconds=edit_seconds)
    assert intake(pilot, edit).archived
    assert pilot.conn.execute(
        "SELECT count(*) FROM source_archive_revisions WHERE telegram_message_id=%s", (original["message"]["message_id"],),
    ).fetchone() == (2,)
    assert not archive.reauthorize(previous, **scope)
    assert pilot.conn.execute("SELECT status,text FROM coaching_outputs WHERE id=%s", (output_id,)).fetchone() == ("cancelled", None)


@pytest.mark.parametrize("seconds_after_join", [0, 1], ids=["same-second-later-update", "later-second"])
def test_delayed_join_preserves_work_originating_after_actual_join(pilot, seconds_after_join):
    original = dated_message(pilot, "Арни, сообщение после фактического добавления бота.",
                             update_id=11, seconds=seconds_after_join)
    assert intake(pilot, original).queued
    job = pilot.repo.claim_job()
    proof = SourceProof(job.source_id, job.source_checksum, job.consent_version)
    reply_id = pilot.repo.enqueue_output(job, "Ожидающий ответ", source_proofs=(proof,))
    control = dated_message(pilot, "/privacy", update_id=12, seconds=seconds_after_join)
    normalized = normalize_update(control, bot_username=pilot.config.bot_username, bot_user_id=str(BOT))
    control_id = pilot.repo.enqueue_control_output(normalized, "Ожидающая служебная квитанция")
    assert reply_id and control_id

    # Telegram delivers the earlier join update only after the later message.
    delayed_join = membership(pilot, old="left", new="member", update_id=10, seconds=0)
    assert intake(pilot, delayed_join).status == "membership_updated"
    assert pilot.conn.execute("SELECT status FROM coach_jobs WHERE id=%s", (job.id,)).fetchone() == ("processing",)
    assert pilot.repo.job_is_current(job)
    assert pilot.conn.execute(
        "SELECT count(*) FROM coaching_outputs WHERE id=ANY(%s) AND status='pending' AND text IS NOT NULL",
        ([reply_id, control_id],),
    ).fetchone() == (2,)
    assert intake(pilot, delayed_join).status == "duplicate"
    assert pilot.repo.job_is_current(job)
