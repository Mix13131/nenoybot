"""Independent regressions for Codex review of PR #171, commit 643efef.

Real PostgreSQL, actual coach intake/worker/ledger, invented messages, and fake
HTTP providers only. The write barrier is before the repository lock so the
author's deletion commits before the old worker attempts its derived write.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_worker import build_worker
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ai import ProviderResponse
from app_v2.services.coach_ingestor import ingest_coach_update


BOT, CHAT, TOPIC, AUTHOR = 909090001, -999000071, 17, 999000072


class SyntheticProvider:
    def __init__(self, *, propose_experiment=False):
        self.calls = []
        self.propose_experiment = propose_experiment

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs['messages'][-1]['content'])
        source = payload['sources'][0]
        extraction = payload['phase'] in {'assessment', 'index'}
        refs = [{'source_id': source['source_id'], 'checksum': source['checksum']}]
        experiment = None
        if self.propose_experiment and not extraction:
            experiment = {
                'instruction': 'Запиши один небольшой следующий шаг в черновик.',
                'observation': 'Заметь, стало ли понятнее, с чего начать.',
                'minutes': 5, 'reversible': True, 'voluntary': True, 'risk_level': 'low',
                'source_refs': refs, 'message_index': 0,
            }
        result = {
            'facts': [{'kind': 'action', 'quote': source['text'][:100],
                       'source_id': source['source_id'], 'checksum': source['checksum']}],
            'hypotheses': [], 'risk_flags': [], 'first_time_claim': False,
            'recurring_claim': False,
        }
        if extraction:
            result['search_terms'] = []
        else:
            text = (experiment['instruction'] + ' ' + experiment['observation']
                    if experiment else 'Какой результат ты заметил?')
            result.update(action='RESPOND', messages=[{'text': text, 'source_refs': refs}],
                          experiments=[experiment] if experiment else [], gaps=[])
        return ProviderResponse(json.dumps(result, ensure_ascii=False), 100, 80, 'synthetic-review-model')


class RecordingSender:
    def __init__(self):
        self.sent = []

    def send(self, output):
        self.sent.append(output)
        return {'message_id': 9000 + len(self.sent), 'chat': {'id': output.chat_id},
                'message_thread_id': output.message_thread_id}


@pytest.fixture
def reviewed_pilot(monkeypatch):
    url = os.getenv('NENOY_V2_TEST_DATABASE_URL')
    if not url:
        pytest.skip('NENOY_V2_TEST_DATABASE_URL is not configured')
    pg = pytest.importorskip('psycopg')
    database = 'coach_external_review_' + uuid4().hex
    with pg.connect(url, autocommit=True) as admin:
        admin.execute(pg.sql.SQL('CREATE DATABASE {}').format(pg.sql.Identifier(database)))
        dedicated_url = pg.conninfo.make_conninfo(url, dbname=database)
        try:
            config = CoachConfig(
                environment='test', enabled=True, ai_enabled=True, database_url=dedicated_url,
                telegram_bot_token=f'{BOT}:synthetic_not_a_real_bot_token',
                webhook_secret='synthetic_review_secret_' * 2, openai_api_key='synthetic-no-live-key',
                bot_user_id=BOT, bot_username='synthetic_review_bot', chat_id=CHAT,
                topic_ids=(TOPIC,), archive_policy='until_author_deletes', transient_ttl_seconds=120,
                ai_model='synthetic-review-model', daily_cap_microusd=10_000_000,
                input_price_microusd_per_million=1_000_000, output_price_microusd_per_million=2_000_000,
                max_input_tokens=500_000, max_output_tokens=2000,
            )
            prepare(config)
            at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=3)
            clock = SimpleNamespace(now=at)
            from app_v2.repositories import coach_repo as repo_module
            original_now = repo_module._now
            monkeypatch.setattr(repo_module, '_now', lambda value=None: original_now(value or clock.now))

            class SyntheticClock:
                @staticmethod
                def now(tz=None):
                    return clock.now

            monkeypatch.setattr('app_v2.coach_worker.datetime', SyntheticClock)
            with pg.connect(dedicated_url, autocommit=True) as conn:
                repo = CoachRepository(conn, chat_id=CHAT, message_thread_id=TOPIC,
                                       bot_user_id=BOT, transient_ttl_seconds=120)
                archive = SourceArchiveRepository(conn)

                def grant(mode):
                    # Explicit per-author settings select the original race
                    # path. No activation/disclosure/publication receipt is
                    # needed under the configured group's default policy.
                    history = mode != 'direct_only'
                    version = archive.set_permissions(
                        chat_id=CHAT, message_thread_id=TOPIC, author_user_id=AUTHOR,
                        actor_user_id=AUTHOR, retain_source=history, historical_inference=history,
                        auto_report_analysis=mode == 'auto_report',
                        unsolicited_group_reply=mode == 'auto_report',
                        direct_request_group_reply=True, now=at - timedelta(hours=1),
                    )
                    conn.execute(
                        'INSERT INTO coach_participant_state(chat_id,message_thread_id,author_user_id,'
                        'track_status) VALUES(%s,%s,%s,%s)',
                        (CHAT, TOPIC, AUTHOR, 'active' if history else 'off'),
                    )
                    return version

                def update(text, *, update_id, message_id, original_at=None, edited_at=None):
                    message = {
                        'message_id': message_id, 'date': int((original_at or at).timestamp()),
                        'from': {'id': AUTHOR, 'is_bot': False},
                        'chat': {'id': CHAT, 'type': 'supergroup', 'is_forum': True},
                        'message_thread_id': TOPIC, 'is_topic_message': True, 'text': text,
                    }
                    if edited_at is not None:
                        message['edit_date'] = int(edited_at.timestamp())
                    return {'update_id': update_id, 'edited_message' if edited_at is not None else 'message': message}

                yield SimpleNamespace(pg=pg, url=dedicated_url, config=config, conn=conn,
                                      repo=repo, archive=archive, at=at, clock=clock,
                                      grant=grant, update=update)
        finally:
            admin.execute(pg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(pg.sql.Identifier(database)))


@pytest.mark.parametrize('mode,write_kind', [
    ('index', 'fact'), ('auto_report', 'fact'), ('direct', 'fact'),
    ('auto_report', 'experiment'), ('direct', 'experiment'),
])
def test_committed_track_deletion_fences_old_worker_derived_writes(reviewed_pilot, monkeypatch, mode, write_kind):
    pilot = reviewed_pilot
    consent_version = pilot.grant(mode)
    provider = SyntheticProvider(propose_experiment=write_kind == 'experiment')
    sender = RecordingSender()
    worker = build_worker(pilot.conn, pilot.config, TOPIC, sender=sender, provider=provider)
    text = 'Отчёт: сделал собственный синтетический черновик.'
    if mode == 'direct':
        text = 'Напарник, ' + text
    original = pilot.update(text, update_id=11, message_id=101)
    accepted = ingest_coach_update(original, conn=pilot.conn, config=pilot.config)
    assert accepted.archived and accepted.queued
    assert pilot.conn.execute('SELECT mode FROM coach_jobs').fetchone() == (mode,)

    entered, release = Event(), Event()
    original_save = worker.repo.save_episode
    fact_writes = 0

    def pause_before_repository_lock(**kwargs):
        nonlocal fact_writes
        is_experiment = kwargs['episode_type'] == 'experiment'
        if not is_experiment:
            fact_writes += 1
        should_pause = (is_experiment if write_kind == 'experiment'
                        else not is_experiment and fact_writes == (1 if mode == 'index' else 2))
        if should_pause:
            entered.set()
            assert release.wait(10), 'deletion did not release the synthetic write barrier'
        return original_save(**kwargs)

    monkeypatch.setattr(worker.repo, 'save_episode', pause_before_repository_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(worker.run_once)
        try:
            assert entered.wait(10), 'worker never reached the intended derived-write boundary'
            # A distinct connection guarantees the deletion commits separately
            # from the suspended worker; no mocked job/permission state is used.
            with pilot.pg.connect(pilot.url, autocommit=True) as deleting:
                if mode != 'index':
                    assert deleting.execute('SELECT count(*) FROM coach_episodes WHERE is_active').fetchone()[0] > 0
                command = pilot.update('/track_delete', update_id=12, message_id=102,
                                       original_at=pilot.at + timedelta(seconds=1))
                result = ingest_coach_update(command, conn=deleting, config=pilot.config)
                assert result.status == 'accepted'
                assert deleting.execute('SELECT count(*) FROM coach_episodes WHERE is_active').fetchone()[0] == 0
                assert deleting.execute('SELECT version FROM source_archive_consents').fetchone() == (consent_version,)
                assert deleting.execute(
                    'SELECT count(*) FROM source_archive_revisions WHERE telegram_message_id=101 AND source_status=\'active\''
                ).fetchone()[0] == 1
        finally:
            release.set()
        assert pending.result(timeout=10) is True

    remaining = pilot.conn.execute(
        'SELECT episode_type,summary,status FROM coach_episodes WHERE is_active OR summary IS NOT NULL'
    ).fetchall()
    assert remaining == [], 'a worker started before /track_delete recreated the deleted derived track'
    assert pilot.conn.execute(
        "SELECT count(*) FROM coaching_outputs WHERE mode<>'control' AND status IN ('pending','sending','sent')"
    ).fetchone()[0] == 0


@pytest.mark.parametrize('after_ttl', [False, True], ids=['immediate-delayed-original', 'ttl-and-new-connection'])
def test_edit_before_original_never_pays_or_sends_superseded_direct_text(reviewed_pilot, after_ttl):
    pilot = reviewed_pilot
    pilot.grant('direct_only')
    provider, sender = SyntheticProvider(), RecordingSender()
    edited_at = pilot.at + timedelta(seconds=5)
    original = pilot.update('Напарник, ORIGINAL_SYNTHETIC_TEXT', update_id=21, message_id=201)
    edit = pilot.update('Напарник, CORRECTED_SYNTHETIC_TEXT', update_id=22, message_id=201,
                        edited_at=edited_at)
    pilot.clock.now = edited_at + timedelta(seconds=1)
    edited = ingest_coach_update(edit, conn=pilot.conn, config=pilot.config)
    assert not edited.archived and not edited.queued
    if after_ttl:
        pilot.clock.now = edited_at + timedelta(seconds=pilot.config.transient_ttl_seconds + 1)
        pilot.repo.maintenance(now=pilot.clock.now)
    else:
        pilot.clock.now += timedelta(seconds=1)

    with pilot.pg.connect(pilot.url, autocommit=True) as restarted:
        worker = build_worker(restarted, pilot.config, TOPIC, sender=sender, provider=provider)
        accepted = ingest_coach_update(original, conn=restarted, config=pilot.config)
        for _ in range(4):
            if not worker.run_once():
                break
        # Assert the user-visible/paid failure first. On reviewed 643efef this
        # executes two paid fake calls and sends the original superseded text.
        assert provider.calls == [], 'superseded direct-only original was paid/analyzed after its edit'
        assert sender.sent == []
        assert not accepted.queued and not accepted.archived
        assert restarted.execute('SELECT count(*) FROM coach_jobs').fetchone()[0] == 0
        assert restarted.execute('SELECT count(*) FROM source_archive_revisions').fetchone()[0] == 0
        watermark = restarted.execute(
            'SELECT telegram_update_id,revision_at,checksum FROM coach_message_versions '
            'WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=201',
            (CHAT, TOPIC, AUTHOR),
        ).fetchone()
        assert watermark == (22, edited_at, None), 'the edit watermark must survive without retaining text or a hash'
