"""Synthetic real PostgreSQL permission, recovery and send-boundary checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime,timedelta,timezone
import os
from threading import Event
from uuid import uuid4

import pytest

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.db.migrations import run_migrations
from app_v2.domain.coach import SourceProof
from app_v2.repositories.coach_repo import CoachConfigurationError,CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_controls import CoachControls


BOT=909090001
TOPIC=17


@dataclass
class Sandbox:
    conn: object
    url: str
    pg: object
    seed: int
    at: datetime

    @property
    def chat(self):return -self.seed
    @property
    def author(self):return self.seed+1
    @property
    def repo(self):return CoachRepository(self.conn,chat_id=self.chat,message_thread_id=TOPIC,bot_user_id=BOT)
    @property
    def archive(self):return SourceArchiveRepository(self.conn)
    @property
    def controls(self):return CoachControls(self.repo,archive_policy="until_author_deletes",bot_username="synthetic_bot")

    def message(self,n=1,text="synthetic own report",*,author=None,chat=None,topic=TOPIC,message_id=None,edit=False,at=None,**extra):
        message={"message_id":message_id or n,"date":int((at or self.at+timedelta(seconds=n)).timestamp()),
                 "from":{"id":author or self.author,"is_bot":False},
                 "chat":{"id":chat or self.chat,"type":"supergroup","is_forum":True},
                 "message_thread_id":topic,"is_topic_message":True,"text":text,**extra}
        if edit:message["edit_date"]=int((self.at+timedelta(seconds=n+100)).timestamp())
        return normalize_update({"update_id":self.seed+n,"edited_message" if edit else "message":message})

    def grant(self,*,author=None,**flags):
        author=author or self.author
        self.archive.set_permissions(chat_id=self.chat,message_thread_id=TOPIC,author_user_id=author,
                                     actor_user_id=author,now=self.at-timedelta(hours=1),**flags)
        self.conn.execute("INSERT INTO coach_participant_state(chat_id,message_thread_id,author_user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                          (self.chat,TOPIC,author))

    def notice(self,n=1):
        normalized=self.message(n,"/privacy")
        result=self.controls.handle(normalized)
        self.repo.enqueue_control_output(normalized,result.reply_text,disclosure=result.disclosure)
        output=self.repo.claim_output()
        assert self.repo.deliver_output(output,lambda item:{"message_id":999})=="sent"

    def source(self,n=1,text="synthetic source"):
        normalized=self.message(n,text)
        capture=self.archive.capture(normalized)
        assert capture.status=="archived"
        permission=self.repo.permissions(self.author)
        return normalized,SourceProof(capture.source_id,capture.checksum,permission.version)

    def direct_job(self,n=1,text="my synthetic request"):
        message=self.message(n,text)
        assert self.repo.enqueue_job(message,"direct")
        job=self.repo.claim_job()
        assert job
        return message,job

    def offer(self,message,proof,proposal="Synthetic optional experiment"):
        identifier=self.repo.save_experiment(author_user_id=self.author,proposal=proposal,source_proofs=[proof])
        assert self.repo.enqueue_job(message,"direct",source_id=proof.source_id)
        job=self.repo.claim_job()
        assert self.repo.enqueue_output(job,proposal,source_proofs=[proof],experiment_id=identifier)
        self.repo.complete_job(job)
        assert self.repo.deliver_output(self.repo.claim_output(),lambda item:{"message_id":98})=="sent"
        return identifier


@pytest.fixture
def coach():
    url=os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pg=pytest.importorskip("psycopg")
    run_migrations(url)
    seed=8_000_000_000+int(uuid4().hex[:10],16)
    with pg.connect(url,autocommit=True) as conn:
        state=Sandbox(conn,url,pg,seed,datetime.now(timezone.utc).replace(microsecond=0)+timedelta(seconds=2))
        state.repo.bind_identity()
        state.archive.configure_scope(state.chat,TOPIC,enabled=True,activated_at=state.at-timedelta(days=1))
        try:yield state
        finally:
            with conn.transaction():
                for table in ("coaching_outputs","coach_jobs","coach_episodes","coach_message_versions",
                              "coach_ingest_receipts","coach_participant_state"):
                    conn.execute(f"DELETE FROM {table} WHERE chat_id=%s",(state.chat,))
                conn.execute("UPDATE source_archive_messages SET latest_source_id=NULL WHERE chat_id=%s",(state.chat,))
                for table in ("source_archive_revisions","source_archive_messages","source_archive_deletions",
                              "source_archive_consent_versions","source_archive_consents","source_archive_scopes"):
                    conn.execute(f"DELETE FROM {table} WHERE chat_id=%s",(state.chat,))


def test_positive_grant_requires_actually_delivered_notice(coach):
    first=coach.message(1,"/archive_on")
    result=coach.controls.handle(first)
    assert result.disclosure and not coach.repo.permissions(coach.author).retain_source
    coach.repo.enqueue_control_output(first,result.reply_text,disclosure=True)
    still=coach.controls.handle(coach.message(2,"/archive_on"))
    assert still.disclosure and not coach.repo.permissions(coach.author).retain_source
    output=coach.repo.claim_output()
    assert coach.repo.deliver_output(output,lambda output:{"message_id":80})=="sent"
    coach.controls.handle(coach.message(3,"/archive_on"))
    permission=coach.repo.permissions(coach.author)
    assert permission.retain_source
    assert not any((permission.historical_inference,permission.auto_report_analysis,
                    permission.unsolicited_group_reply,permission.direct_request_group_reply))


def test_direct_only_is_usable_without_retention_or_history(coach):
    coach.notice()
    coach.controls.handle(coach.message(2,"/direct_on"))
    permission=coach.repo.permissions(coach.author)
    assert permission.direct_request_group_reply and not permission.retain_source and not permission.historical_inference
    _,job=coach.direct_job(3)
    assert coach.repo.enqueue_output(job,"Synthetic direct-only answer")
    coach.repo.complete_job(job)
    output=coach.repo.claim_output()
    assert coach.repo.deliver_output(output,lambda out:{"message_id":81})=="sent"
    assert coach.conn.execute("SELECT current_text FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()==(None,)
    assert coach.conn.execute("SELECT text,source_proofs FROM coaching_outputs WHERE id=%s",(output.id,)).fetchone()==(None,[])
    assert coach.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE chat_id=%s",(coach.chat,)).fetchone()[0]==0


def test_auto_requirements_and_pause_preserve_independent_grants(coach):
    coach.notice()
    result=coach.controls.handle(coach.message(2,"/auto_on"))
    assert "сначала" in result.reply_text
    assert not coach.repo.permissions(coach.author).retain_source
    for n,command in enumerate(("/archive_on","/history_on","/direct_on","/auto_on","/track_start"),3):
        coach.controls.handle(coach.message(n,command))
    assert coach.repo.permissions(coach.author).automatic_allowed
    coach.controls.handle(coach.message(9,"/stop"))
    permission=coach.repo.permissions(coach.author)
    assert permission.retain_source and permission.historical_inference and permission.direct_request_group_reply
    assert not permission.automatic_allowed and permission.track_status=="paused"


def test_other_author_topic_forged_actor_and_quoted_commands_never_grant(coach):
    assert not coach.controls.handle(coach.message(1,"/archive_on",topic=18)).handled
    assert not coach.controls.handle(coach.message(2,"/archive_on",forward_origin={"type":"user"})).handled
    assert not coach.controls.handle(coach.message(3,"/archive_on",quote={"text":"old command"})).handled
    original=coach.message(4,"/archive_on")
    forged=replace(original,envelope=original.envelope.model_copy(update={"actor_user_id":str(coach.author+1)}))
    assert not coach.controls.handle(forged).handled
    coach.notice(5)
    coach.controls.handle(coach.message(6,"/archive_on"))
    assert coach.repo.permissions(coach.author).retain_source
    assert coach.repo.permissions(coach.author+1).version==0


def test_replayed_and_late_control_cannot_undo_newer_revocation(coach):
    coach.notice()
    on=coach.message(2,"/archive_on")
    coach.controls.handle(on)
    version=coach.repo.permissions(coach.author).version
    assert coach.controls.handle(on).reply_text is None
    assert coach.repo.permissions(coach.author).version==version
    coach.controls.handle(coach.message(4,"/archive_off"))
    assert coach.controls.handle(coach.message(3,"/archive_on")).reply_text is None
    assert not coach.repo.permissions(coach.author).retain_source


def test_disabled_scope_still_allows_self_forget_and_control_receipt(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    coach.source()
    coach.archive.configure_scope(coach.chat,TOPIC,enabled=False)
    update=coach.message(2,"/forget")
    result=coach.controls.handle(update)
    assert result.handled and "сообщений 1" in result.reply_text
    coach.repo.enqueue_control_output(update,result.reply_text)
    assert coach.repo.deliver_output(coach.repo.claim_output(),lambda out:{"message_id":82})=="sent"
    result=coach.controls.handle(coach.message(3,"/archive_on"))
    assert "временно отключена" in result.reply_text


def test_public_track_confirmation_is_an_explicit_separate_request(coach):
    coach.notice()
    for n,command in enumerate(("/archive_on","/history_on","/direct_on","/track_start"),2):
        coach.controls.handle(coach.message(n,command))
    draft=coach.controls.handle(coach.message(6,"/track 30"))
    assert draft.job_mode is None and not draft.public_confirmed and "/track 30 confirm" in draft.reply_text
    confirmed=coach.controls.handle(coach.message(7,"/track 30 confirm"))
    assert (confirmed.job_mode,confirmed.window_days,confirmed.public_confirmed)==("track",30,True)


def test_intake_transaction_rolls_back_receipts_grants_and_replies(coach):
    coach.notice()
    before=coach.repo.permissions(coach.author)
    update=coach.message(2,"/archive_on")
    with pytest.raises(RuntimeError):
        with coach.conn.transaction():
            assert coach.repo.claim_ingest(update.telegram_update_id)
            result=coach.controls.handle(update)
            coach.repo.enqueue_control_output(update,result.reply_text)
            raise RuntimeError("synthetic crash before ACK")
    assert coach.repo.permissions(coach.author)==before
    assert coach.repo.claim_ingest(update.telegram_update_id)
    assert not coach.repo.claim_ingest(update.telegram_update_id)
    assert coach.repo.claim_output() is None


def test_edit_of_direct_only_request_scrubs_jobs_and_prepared_answer(coach):
    coach.grant(direct_request_group_reply=True)
    message,job=coach.direct_job()
    assert coach.repo.enqueue_output(job,"Do not send after edit")
    output=coach.repo.claim_output()
    edited=coach.message(2,"changed request",message_id=1,edit=True,at=message.envelope.occurred_at)
    assert coach.repo.invalidate_message(archive_message_from_update(edited))
    calls=[]
    assert coach.repo.deliver_output(output,lambda item:calls.append(item))=="cancelled"
    assert calls==[]
    assert coach.conn.execute("SELECT current_text FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()==(None,)
    assert coach.conn.execute("SELECT text FROM coaching_outputs WHERE id=%s",(output.id,)).fetchone()==(None,)


def test_historical_source_edit_invalidates_derived_episode_and_queued_answer(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    source,proof=coach.source()
    episode=coach.repo.save_episode(author_user_id=coach.author,episode_type="action",summary="Synthetic action",source_proofs=[proof])
    _,job=coach.direct_job(2)
    assert coach.repo.enqueue_output(job,"Synthetic historical answer",source_proofs=[proof])
    output=coach.repo.claim_output()
    coach.archive.capture(coach.message(3,"corrected source",message_id=1,at=source.envelope.occurred_at,edit=True))
    assert coach.repo.deliver_output(output,lambda item:pytest.fail("stale source sent"))=="cancelled"
    assert coach.repo.list_episodes(author_user_id=coach.author)==[]
    assert coach.conn.execute("SELECT summary FROM coach_episodes WHERE id=%s",(episode,)).fetchone()==(None,)


def test_direct_current_archived_source_does_not_require_history_permission(coach):
    coach.grant(retain_source=True,direct_request_group_reply=True)
    message,proof=coach.source()
    assert coach.repo.enqueue_job(message,"direct",source_id=proof.source_id)
    job=coach.repo.claim_job()
    assert coach.repo.enqueue_output(job,"Synthetic current-only reply",source_proofs=[proof])
    assert coach.repo.deliver_output(coach.repo.claim_output(),lambda out:{"message_id":90})=="sent"


def test_wrong_history_proof_or_removed_proof_cannot_cross_send_guard(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    _,proof=coach.source()
    _,job=coach.direct_job(2)
    assert coach.repo.enqueue_output(job,"not allowed",source_proofs=[replace(proof,checksum="0"*64)]) is None
    assert coach.repo.enqueue_output(job,"valid",source_proofs=[proof])
    output=coach.repo.claim_output()
    altered=replace(output,source_proofs=())
    assert coach.repo.deliver_output(altered,lambda item:pytest.fail("Caller removed the evidence"))=="cancelled"


def test_timeout_is_uncertain_scrubbed_and_never_retried(coach):
    coach.grant(direct_request_group_reply=True)
    _,job=coach.direct_job()
    coach.repo.enqueue_output(job,"synthetic output")
    output=coach.repo.claim_output()
    def timeout(item):raise TimeoutError("synthetic ambiguous HTTP outcome")
    assert coach.repo.deliver_output(output,timeout)=="uncertain"
    assert coach.repo.claim_output() is None
    assert coach.conn.execute("SELECT status,text FROM coaching_outputs WHERE id=%s",(output.id,)).fetchone()==("uncertain",None)


def test_job_leases_fence_late_workers_and_sending_crashes_do_not_duplicate(coach):
    coach.grant(direct_request_group_reply=True)
    at=datetime.now(timezone.utc)
    coach.repo.enqueue_job(coach.message(),"direct",now=at)
    first=coach.repo.claim_job(lease_seconds=1,now=at)
    second=coach.repo.claim_job(now=at+timedelta(seconds=2))
    assert first.id==second.id and first.lease_token!=second.lease_token
    assert coach.repo.enqueue_output(first,"late worker",now=at+timedelta(seconds=2)) is None
    coach.repo.complete_job(first)
    assert coach.repo.job_is_current(second,now=at+timedelta(seconds=2))
    assert coach.repo.enqueue_output(second,"new worker",now=at+timedelta(seconds=2))
    output=coach.repo.claim_output(lease_seconds=1,now=at+timedelta(seconds=2))
    assert coach.repo.claim_output(now=at+timedelta(seconds=4)) is None
    assert coach.conn.execute("SELECT status,text FROM coaching_outputs WHERE id=%s",(output.id,)).fetchone()==("uncertain",None)


def test_ttl_scrubs_pending_payloads_without_touching_authorized_archive(coach):
    coach.grant(retain_source=True,direct_request_group_reply=True)
    coach.source()
    _,job=coach.direct_job(2)
    coach.repo.enqueue_output(job,"transient output")
    report=coach.repo.maintenance(now=datetime.now(timezone.utc)+timedelta(days=2))
    assert report=={"expired_jobs":1,"expired_or_uncertain_outputs":1}
    assert coach.conn.execute("SELECT current_text FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()==(None,)
    assert coach.conn.execute("SELECT available_text FROM source_archive_revisions WHERE chat_id=%s",(coach.chat,)).fetchone()==("synthetic source",)


def test_final_send_serializes_with_concurrent_revocation(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    coach.repo.enqueue_job(message,"direct",source_id=proof.source_id)
    job=coach.repo.claim_job()
    coach.repo.enqueue_output(job,"synthetic send",source_proofs=[proof])
    output=coach.repo.claim_output()
    entered=Event(); release=Event(); revoke_started=Event()
    def send():
        with coach.pg.connect(coach.url,autocommit=True) as connection:
            repo=CoachRepository(connection,chat_id=coach.chat,message_thread_id=TOPIC,bot_user_id=BOT)
            def network(item):
                entered.set()
                assert release.wait(5)
                return {"message_id":91}
            return repo.deliver_output(output,network)
    def revoke():
        with coach.pg.connect(coach.url,autocommit=True) as connection:
            revoke_started.set()
            return SourceArchiveRepository(connection).set_permissions(chat_id=coach.chat,message_thread_id=TOPIC,
                author_user_id=coach.author,actor_user_id=coach.author,direct_request_group_reply=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending=pool.submit(send)
        assert entered.wait(5)
        revoking=pool.submit(revoke)
        assert revoke_started.wait(5)
        assert not revoking.done()
        release.set()
        assert sending.result(5)=="sent"
        assert revoking.result(5)>job.consent_version
    assert coach.repo.claim_output() is None
    assert not coach.repo.permissions(coach.author).retain_source


def test_experiments_are_source_backed_and_completion_is_self_reported(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    identifier=coach.offer(message,proof)
    assert coach.repo.list_experiments(author_user_id=coach.author)[0]["status"]=="offered"
    _,feedback=coach.source(2,"I tried the synthetic experiment")
    assert coach.repo.set_experiment_status(author_user_id=coach.author,experiment_id=identifier,
                                            status="tried_self_reported",source_proofs=[feedback])
    assert coach.repo.list_experiments(author_user_id=coach.author)[0]["status"]=="tried_self_reported"
    assert not coach.repo.set_experiment_status(author_user_id=coach.author+1,experiment_id=identifier,
                                                status="accepted",source_proofs=[feedback])


def test_database_identity_cannot_be_silently_rebound(coach):
    coach.repo.assert_identity()
    foreign=CoachRepository(coach.conn,chat_id=coach.chat,message_thread_id=TOPIC,bot_user_id=BOT+1)
    with pytest.raises(CoachConfigurationError):foreign.assert_identity()
    with pytest.raises(CoachConfigurationError):foreign.bind_identity()


def test_public_review_reply_requires_confirm_but_deterministic_notice_does_not(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    coach.repo.enqueue_job(message,"weekly_review",source_id=proof.source_id,public_confirmed=False)
    job=coach.repo.claim_job()
    assert coach.repo.enqueue_output(job,"Private review prematurely",source_proofs=[proof]) is None
    assert coach.repo.enqueue_output(job,"Synthetic explicit publication confirmation notice",kind="notice")
    assert coach.repo.deliver_output(coach.repo.claim_output(),lambda out:{"message_id":92})=="sent"


def test_index_mode_never_creates_a_public_reply(coach):
    coach.grant(retain_source=True,historical_inference=True)
    message,proof=coach.source()
    coach.repo.enqueue_job(message,"index",source_id=proof.source_id)
    job=coach.repo.claim_job()
    assert job.mode=="index"
    assert coach.repo.enqueue_output(job,"must stay silent",source_proofs=[proof]) is None


def test_ask_command_is_direct_and_requires_only_its_separate_grant(coach):
    unavailable=coach.controls.handle(coach.message(1,"/ask my request"))
    assert unavailable.disclosure and unavailable.job_mode is None
    coach.notice(2)
    coach.controls.handle(coach.message(3,"/direct_on"))
    direct=coach.controls.handle(coach.message(4,"/ask@synthetic_bot my request"))
    assert direct.job_mode=="direct" and direct.handled
    assert not coach.repo.permissions(coach.author).retain_source


def test_experiment_controls_use_own_authenticated_feedback_source(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    identifier=coach.offer(message,proof)
    feedback=coach.message(2,"/tried "+identifier)
    assert coach.archive.capture(feedback).status=="archived"
    result=coach.controls.handle(feedback)
    assert "сообщил, что попробовал" in result.reply_text
    items=coach.repo.list_experiments(author_user_id=coach.author,since=feedback.envelope.occurred_at)
    assert len(items)==1 and items[0]["status"]=="tried_self_reported"
    assert len(items[0]["source_proofs"])==2
    # A repeated proposal cannot reset the self-report or drop its feedback proof.
    assert coach.repo.save_experiment(author_user_id=coach.author,proposal="Synthetic optional experiment",source_proofs=[proof])==identifier
    assert coach.repo.list_experiments(author_user_id=coach.author)[0]["status"]=="tried_self_reported"
    assert len(coach.repo.list_experiments(author_user_id=coach.author)[0]["source_proofs"])==2


def test_unrelated_output_permission_changes_keep_authorized_memory(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    _,proof=coach.source()
    coach.repo.save_episode(author_user_id=coach.author,episode_type="goal",summary="Synthetic sourced goal",source_proofs=[proof])
    coach.archive.set_permissions(chat_id=coach.chat,message_thread_id=TOPIC,author_user_id=coach.author,
                                  actor_user_id=coach.author,retain_source=True,historical_inference=True,direct_request_group_reply=False)
    episodes=coach.repo.list_episodes(author_user_id=coach.author)
    assert len(episodes)==1
    assert episodes[0]["source_proofs"][0].access_version==coach.repo.permissions(coach.author).version
    assert episodes[0]["source_proofs"][0].access_version!=proof.access_version


def test_later_optin_does_not_authorize_a_delayed_old_direct_request(coach):
    coach.grant(direct_request_group_reply=True)
    old=coach.message(1,at=coach.at-timedelta(days=2))
    assert coach.repo.enqueue_job(old,"direct") is None


def test_disabled_topic_cancels_existing_work_and_cannot_replay_on_reenable(coach):
    coach.grant(direct_request_group_reply=True)
    original,job=coach.direct_job()
    assert coach.repo.enqueue_output(job,"Old reply before topic shutdown")
    output=coach.repo.claim_output()
    coach.archive.configure_scope(coach.chat,TOPIC,enabled=False)
    assert not coach.repo.job_is_current(job)
    assert coach.repo.enqueue_job(coach.message(2),"direct") is None
    assert coach.repo.enqueue_output(job,"AI returned after scope shutdown") is None
    assert coach.repo.enqueue_output(job,"Deterministic outage notice after shutdown",kind="notice") is None
    edited=coach.message(3,"new text while disabled",message_id=1,edit=True,at=original.envelope.occurred_at)
    assert coach.repo.invalidate_message(archive_message_from_update(edited))
    assert coach.conn.execute("SELECT checksum FROM coach_message_versions WHERE chat_id=%s",(coach.chat,)).fetchone()==(None,)
    coach.archive.configure_scope(coach.chat,TOPIC,enabled=True)
    assert not coach.repo.job_is_current(job)
    assert coach.repo.deliver_output(output,lambda item:pytest.fail("shutdown output replayed"))=="cancelled"
    assert coach.repo.claim_job() is None and coach.repo.claim_output() is None


def test_track_delete_preserves_sources_and_grants_but_removes_derived_track(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    coach.repo.set_track_status(coach.author,"active")
    message,proof=coach.source()
    coach.repo.save_episode(author_user_id=coach.author,episode_type="goal",summary="Own synthetic goal",source_proofs=[proof])
    coach.repo.save_experiment(author_user_id=coach.author,proposal="Unsent synthetic experiment",source_proofs=[proof])
    assert coach.repo.enqueue_job(message,"track",source_id=proof.source_id,public_confirmed=True)
    track=coach.repo.claim_job()
    coach.repo.enqueue_output(track,"Synthetic track",source_proofs=[proof])
    output=coach.repo.claim_output()
    other,other_proof=coach.source(2,"Another own source")
    assert coach.repo.enqueue_job(other,"direct",source_id=other_proof.source_id)
    direct=coach.repo.claim_job()
    assert coach.repo.enqueue_output(direct,"Direct response that already used the old track",source_proofs=[proof,other_proof])
    direct_output=coach.repo.claim_output()
    indexed,indexed_proof=coach.source(3,"Source waiting for indexing")
    assert coach.repo.enqueue_job(indexed,"index",source_id=indexed_proof.source_id)
    before=coach.repo.permissions(coach.author)
    result=coach.controls.handle(coach.message(4,"/track_delete"))
    assert "Исходный архив" in result.reply_text
    after=coach.repo.permissions(coach.author)
    assert replace(after,track_status="active")==before and after.track_status=="off"
    assert coach.repo.list_episodes(author_user_id=coach.author)==[]
    assert coach.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE chat_id=%s AND available_text IS NOT NULL",(coach.chat,)).fetchone()==(3,)
    assert not coach.repo.job_is_current(track)
    assert coach.repo.claim_job() is None
    assert coach.repo.deliver_output(output,lambda item:pytest.fail("deleted track sent"))=="cancelled"
    assert not coach.repo.job_is_current(direct)
    assert coach.repo.deliver_output(direct_output,lambda item:pytest.fail("old track leaked through a direct response"))=="cancelled"
    # Independent direct-only use remains possible after deleting the track.
    assert coach.repo.enqueue_job(coach.message(5,"/ask own new question"),"direct")


@pytest.mark.parametrize("send_ok",[True,False])
def test_prepared_experiment_is_hidden_until_successful_send(coach,send_ok):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    identifier=coach.repo.save_experiment(author_user_id=coach.author,proposal="Synthetic prepared proposal",source_proofs=[proof])
    assert coach.repo.list_experiments(author_user_id=coach.author)==[]
    assert coach.repo.list_episodes(author_user_id=coach.author)==[]
    _,feedback=coach.source(2,"Synthetic feedback before the offer was sent")
    assert not coach.repo.set_experiment_status(author_user_id=coach.author,experiment_id=identifier,
                                                status="accepted",source_proofs=[feedback])
    coach.repo.enqueue_job(message,"direct",source_id=proof.source_id)
    job=coach.repo.claim_job()
    assert coach.repo.enqueue_output(job,"Synthetic prepared proposal",source_proofs=[proof],experiment_id=identifier)
    output=coach.repo.claim_output()
    assert output.experiment_id==identifier
    def sender(item):
        if not send_ok:raise TimeoutError("synthetic uncertain delivery")
        return {"message_id":101}
    assert coach.repo.deliver_output(output,sender)==("sent" if send_ok else "uncertain")
    assert coach.conn.execute("SELECT status FROM coach_episodes WHERE id=%s",(identifier,)).fetchone()==("offered" if send_ok else "prepared",)
    assert len(coach.repo.list_experiments(author_user_id=coach.author))==int(send_ok)


def test_experiment_feedback_cancels_prepared_answers_without_changing_sources_or_grants(coach):
    coach.grant(retain_source=True,historical_inference=True,direct_request_group_reply=True)
    message,proof=coach.source()
    identifier=coach.offer(message,proof)
    _,job=coach.direct_job(2,"Question while experiment is only offered")
    coach.repo.enqueue_output(job,"Answer with old experiment state",source_proofs=[proof])
    output=coach.repo.claim_output()
    feedback=coach.message(3,"/decline "+identifier)
    assert coach.archive.capture(feedback).status=="archived"
    before=coach.repo.permissions(coach.author)
    result=coach.controls.handle(feedback)
    assert "отказываюсь" in result.reply_text
    assert coach.repo.permissions(coach.author)==before
    assert not coach.repo.job_is_current(job)
    assert coach.repo.deliver_output(output,lambda item:pytest.fail("obsolete experiment state sent"))=="cancelled"
    assert coach.conn.execute("SELECT count(*) FROM source_archive_revisions WHERE chat_id=%s AND available_text IS NOT NULL",(coach.chat,)).fetchone()==(2,)
    coach.repo.enqueue_control_output(feedback,result.reply_text)
    assert coach.repo.deliver_output(coach.repo.claim_output(),lambda item:{"message_id":102})=="sent"


def test_maintenance_scrubs_removed_topic_payloads_in_same_bound_bot_database(coach):
    other_topic=TOPIC+1
    coach.archive.configure_scope(coach.chat,other_topic,enabled=True,activated_at=coach.at-timedelta(days=1))
    coach.archive.set_permissions(chat_id=coach.chat,message_thread_id=other_topic,author_user_id=coach.author,
                                  actor_user_id=coach.author,direct_request_group_reply=True,now=coach.at-timedelta(hours=1))
    removed=CoachRepository(coach.conn,chat_id=coach.chat,message_thread_id=other_topic,bot_user_id=BOT)
    assert removed.enqueue_job(coach.message(1,topic=other_topic),"direct")
    job=removed.claim_job()
    assert removed.enqueue_output(job,"Synthetic payload in a no-longer-configured topic")
    coach.repo.maintenance(now=datetime.now(timezone.utc)+timedelta(days=2))
    assert coach.conn.execute("SELECT status,current_text FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()==("expired",None)
    assert coach.conn.execute("SELECT status,text FROM coaching_outputs WHERE job_id=%s",(job.id,)).fetchone()==("expired",None)
    assert coach.conn.execute("SELECT checksum FROM coach_message_versions WHERE chat_id=%s AND message_thread_id=%s",(coach.chat,other_topic)).fetchone()==(None,)
