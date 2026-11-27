"""Explicit one-job recovery against disposable PostgreSQL, with synthetic I/O."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_worker import build_worker
from app_v2.repositories.coach_budget_repo import CoachBudgetRepository
from app_v2.repositories.coach_repo import CoachConfigurationError, CoachRepository
from app_v2.repositories.coach_scope_repo import CoachMembership, CoachScopeRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ingestor import ingest_coach_update
from app_v2.services.coach_recovery import recover_completed_direct
from tests_v2.integration.test_coach_lifecycle_postgres import (
    dedicated_database, RecordingSender, SyntheticProvider,
)


BOT, CHAT, AUTHOR, TOPIC = 906060001, -1000000086000, 906060002, 17


@pytest.fixture
def lost(dedicated_database):
    pg, url = dedicated_database
    config = CoachConfig(
        environment="test", enabled=True, ai_enabled=True, group_mode="joined_groups",
        database_url=url, telegram_bot_token=f"{BOT}:synthetic_not_live",
        webhook_secret="synthetic_recovery_secret_"+"s"*32, openai_api_key="synthetic",
        bot_user_id=BOT, bot_username="recovery_synthetic_bot", ai_model="synthetic-model",
        daily_cap_microusd=10_000_000, input_price_microusd_per_million=1_000_000,
        output_price_microusd_per_million=2_000_000,
        max_input_tokens=500_000, max_output_tokens=2000,
    )
    prepare(config)
    at = datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=1)
    with pg.connect(url, autocommit=True) as conn:
        repo = CoachRepository(conn, chat_id=CHAT, message_thread_id=TOPIC,
                               bot_user_id=BOT, allow_group_messages=True)
        archive = SourceArchiveRepository(conn, allow_group_messages=True)
        budget = CoachBudgetRepository(url)
        original_text = "@recovery_synthetic_bot помоги разобрать синтетическую задачу"

        def intake(*, identifier=100, update_id=5001, author=AUTHOR, text=original_text, edit=False):
            message = {"message_id": identifier, "date": int(at.timestamp()), "text": text,
                       "from": {"id": author, "is_bot": False},
                       "chat": {"id": CHAT, "type": "supergroup", "is_forum": True},
                       "message_thread_id": TOPIC, "is_topic_message": True}
            if edit:
                message["edit_date"] = int(datetime.now(timezone.utc).timestamp())
            return ingest_coach_update({"update_id": update_id,
                                        "edited_message" if edit else "message": message},
                                       conn=conn, config=config)

        def operation(job_id, *, phase="assessment", status="settled", recovery=False):
            raw = "coach-job:"+job_id+(":recovery:1" if recovery else "")
            op_id = "coach:"+hashlib.sha256(raw.encode()).hexdigest()+":direct:"+phase
            budget.reserve(operation_id=op_id, purpose=phase, model="synthetic-model", request_hash="a"*64,
                           daily_cap_microusd=10_000_000, max_input_tokens=100, max_output_tokens=100,
                           input_price_microusd_per_million=1_000_000,
                           output_price_microusd_per_million=2_000_000)
            if status == "settled":
                budget.reconcile(op_id, input_tokens=10, output_tokens=10, outcome="valid_json")
            elif status == "uncertain":
                budget.mark_uncertain(op_id, outcome="provider_error")
            return op_id

        assert intake().queued
        job = repo.claim_job()
        assert job and job.mode == "direct"
        op_id = operation(job.id)
        repo.complete_job(job)
        original = conn.execute("SELECT * FROM coach_ai_operations WHERE operation_id=%s", (op_id,)).fetchone()

        def recover(*, dry_run=True, current=config, **kwargs):
            return recover_completed_direct(current, since=at-timedelta(seconds=1),
                                            until=at+timedelta(seconds=1), expected_chat_id=CHAT,
                                            dry_run=dry_run, **kwargs)

        def add_output(status, *, control=False):
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO coaching_outputs(id,dedupe_key,bundle_id,job_id,chat_id,message_thread_id,"
                "author_user_id,request_message_id,mode,kind,status,expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (identifier, identifier, identifier, None if control else job.id, CHAT, TOPIC, AUTHOR,
                 100, "control" if control else "direct", "notice", status,
                 datetime.now(timezone.utc)+timedelta(hours=1)),
            )

        yield SimpleNamespace(pg=pg, url=url, config=config, conn=conn, repo=repo, archive=archive,
                              budget=budget, at=at, job=job, op_id=op_id, original=original,
                              recover=recover, intake=intake, operation=operation,
                              add_output=add_output, original_text=original_text)


def test_preview_is_read_only_and_discloses_only_allowlisted_counts(lost):
    before = lost.conn.execute("SELECT * FROM coach_jobs").fetchall()
    result = lost.recover(connection_factory=lambda: lost.pg.connect(
        lost.url, autocommit=True, options="-c default_transaction_read_only=on -c timezone=Asia/Tokyo"))
    assert result["status"] == "preview" and result["counts"]["eligible"] == 1
    assert result["counts"]["rearmed"] == 0
    assert lost.conn.execute("SELECT * FROM coach_jobs").fetchall() == before
    serialized = json.dumps(result)
    for private in (lost.original_text, lost.job.id, str(AUTHOR), str(CHAT), lost.op_id):
        assert private not in serialized
    assert lost.conn.execute("SELECT * FROM coach_ai_operations WHERE operation_id=%s", (lost.op_id,)).fetchone() == lost.original


def test_apply_preserves_identity_expiry_and_paid_ledger_then_recovers_exactly_once(lost):
    before = lost.conn.execute("SELECT id,telegram_update_id,created_at,expires_at,attempts FROM coach_jobs").fetchone()
    result = lost.recover(dry_run=False)
    assert result["status"] == "rearmed" and result["counts"]["rearmed"] == 1
    after = lost.conn.execute("SELECT id,telegram_update_id,created_at,expires_at,attempts FROM coach_jobs").fetchone()
    assert after == before
    assert lost.conn.execute("SELECT status,current_text,source_id,source_checksum,recovery_attempt,"
                             "recovery_requested_at IS NOT NULL FROM coach_jobs").fetchone() == (
        "pending", lost.original_text, lost.job.source_id, lost.job.source_checksum, 1, True,
    )
    repeat = lost.recover(dry_run=False)
    assert repeat["counts"]["rearmed"] == 0 and repeat["counts"]["already_recovered"] == 1
    assert lost.conn.execute("SELECT * FROM coach_ai_operations WHERE operation_id=%s", (lost.op_id,)).fetchone() == lost.original

    provider, sender = SyntheticProvider(), RecordingSender()
    worker = build_worker(lost.conn, lost.config, TOPIC, chat_id=CHAT, provider=provider, sender=sender)
    assert worker.run_once()  # new assessment + generation, under recovery namespace
    assert worker.run_once()  # guarded delivery
    assert not worker.run_once()
    assert len(provider.calls) == 2 and len(sender.sent) == 1
    prefix = "coach:"+hashlib.sha256(("coach-job:"+lost.job.id+":recovery:1").encode()).hexdigest()+":direct:"
    operations = lost.conn.execute("SELECT operation_id FROM coach_ai_operations ORDER BY operation_id").fetchall()
    assert {row[0] for row in operations} == {lost.op_id, prefix+"assessment", prefix+"generation"}
    assert lost.conn.execute("SELECT * FROM coach_ai_operations WHERE operation_id=%s", (lost.op_id,)).fetchone() == lost.original
    assert lost.conn.execute("SELECT count(*) FROM coach_jobs").fetchone() == (1,)
    assert lost.conn.execute("SELECT status,current_text,recovery_attempt,recovery_requested_at IS NOT NULL "
                             "FROM coach_jobs").fetchone() == ("completed", None, 1, True)
    assert lost.recover(dry_run=False)["counts"]["already_recovered"] == 1


@pytest.mark.parametrize("status", ["pending", "sending", "sent", "cancelled", "uncertain", "expired"])
def test_any_existing_output_blocks_recovery_even_if_no_text_survives(lost, status):
    lost.add_output(status)
    assert lost.recover()["reason"] == "output_already_exists"
    assert lost.recover(dry_run=False)["counts"]["rearmed"] == 0
    assert lost.conn.execute("SELECT status,recovery_attempt FROM coach_jobs").fetchone() == ("completed", 0)


def test_control_output_with_null_job_id_also_blocks_a_duplicate_reply(lost):
    lost.add_output("sent", control=True)
    assert lost.recover(dry_run=False)["reason"] == "output_already_exists"


@pytest.mark.parametrize("change", ["revoke", "same_grants_new_version", "forget", "scope_disable",
                                     "job_expired", "version_expired", "version_checksum", "new_edit",
                                     "source_corruption", "membership_rejoin", "membership_same_second"])
def test_revocation_edit_expiry_and_lifecycle_changes_fail_closed(lost, change):
    now = datetime.now(timezone.utc)
    if change in {"revoke", "same_grants_new_version"}:
        keep = change == "same_grants_new_version"
        lost.archive.set_permissions(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                                     actor_user_id=AUTHOR, retain_source=keep, historical_inference=keep,
                                     direct_request_group_reply=keep, auto_report_analysis=keep,
                                     unsolicited_group_reply=keep, now=now)
    elif change == "forget":
        lost.archive.forget_sources(chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                                    actor_user_id=AUTHOR, telegram_message_id=100, now=now)
    elif change == "scope_disable":
        lost.archive.configure_scope(CHAT, TOPIC, enabled=False, activated_at=now)
    elif change == "job_expired":
        lost.conn.execute("UPDATE coach_jobs SET expires_at=%s", (now-timedelta(seconds=1),))
    elif change == "version_expired":
        lost.conn.execute("UPDATE coach_message_versions SET expires_at=%s", (now-timedelta(seconds=1),))
    elif change == "version_checksum":
        lost.conn.execute("UPDATE coach_message_versions SET checksum=%s", ("0"*64,))
    elif change == "new_edit":
        assert lost.intake(update_id=5002, text="Changed request", edit=True).archived
    elif change == "source_corruption":
        lost.conn.execute("UPDATE source_archive_revisions SET available_text='Changed without a checksum'")
    elif change == "membership_rejoin":
        registry = CoachScopeRepository(lost.conn, bot_user_id=BOT)
        registry.apply_membership(CoachMembership(CHAT, 6001, now, False, True))
        registry.apply_membership(CoachMembership(CHAT, 6002, now+timedelta(seconds=1), True, False))
    else:
        CoachScopeRepository(lost.conn, bot_user_id=BOT).apply_membership(
            CoachMembership(CHAT, 6001, lost.at, True, True))
    assert lost.recover()["counts"]["eligible"] == 0
    assert lost.recover(dry_run=False)["counts"]["rearmed"] == 0
    assert lost.conn.execute("SELECT recovery_attempt FROM coach_jobs").fetchone() == (0,)


def test_missing_membership_preview_and_apply_never_create_an_active_group(lost):
    lost.conn.execute("DELETE FROM coach_group_memberships WHERE chat_id=%s", (CHAT,))
    assert lost.recover()["reason"] == "group_inactive_or_unknown"
    assert lost.recover(dry_run=False)["reason"] == "group_inactive_or_unknown"
    assert lost.conn.execute("SELECT count(*) FROM coach_group_memberships").fetchone() == (0,)


@pytest.mark.parametrize("phase,status", [("generation", "reserved"), ("generation", "uncertain"),
                                         ("generation", "settled")])
def test_any_original_generation_attempt_is_excluded_even_without_output(lost, phase, status):
    lost.operation(lost.job.id, phase=phase, status=status)
    assert lost.recover()["reason"] == "original_operation_not_assessment_only"
    assert lost.recover(dry_run=False)["counts"]["rearmed"] == 0


def test_missing_or_unverified_original_assessment_and_existing_recovery_ledger_block(lost):
    lost.conn.execute("UPDATE coach_ai_operations SET outcome='invalid_json' WHERE operation_id=%s", (lost.op_id,))
    assert lost.recover()["reason"] == "original_assessment_not_verified"
    lost.conn.execute("UPDATE coach_ai_operations SET outcome='valid_json' WHERE operation_id=%s", (lost.op_id,))
    lost.operation(lost.job.id, recovery=True)
    assert lost.recover(dry_run=False)["reason"] == "recovery_operation_already_attempted"


def test_wrong_bot_identity_disallowed_scope_and_ambiguous_window_do_not_rearm(lost):
    with pytest.raises(CoachConfigurationError):
        lost.recover(current=replace(lost.config, bot_user_id=BOT+1))
    denied = replace(lost.config, group_mode="configured", chat_id=CHAT, topic_ids=(TOPIC+1,))
    assert lost.recover(current=denied)["reason"] == "scope_not_allowed"
    assert lost.intake(identifier=101, update_id=5002).queued
    other = lost.repo.claim_job()
    assert other
    lost.operation(other.id)
    lost.repo.complete_job(other)
    result = lost.recover(dry_run=False)
    assert result["reason"] == "ambiguous_direct_requests"
    assert result["counts"]["candidates_seen"] == 2 and result["counts"]["rearmed"] == 0
    assert lost.conn.execute("SELECT sum(recovery_attempt) FROM coach_jobs").fetchone() == (0,)


def test_concurrent_operator_applies_create_one_attempt_without_extra_jobs(lost):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: lost.recover(dry_run=False), range(2)))
    assert sum(r["counts"]["rearmed"] for r in results) == 1
    assert sum(r["counts"]["already_recovered"] for r in results) == 1
    assert lost.conn.execute("SELECT count(*),sum(recovery_attempt) FROM coach_jobs").fetchone() == (1, 1)
    assert lost.conn.execute("SELECT count(*) FROM coach_ai_operations").fetchone() == (1,)
