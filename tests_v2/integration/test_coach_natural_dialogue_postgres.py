"""Product acceptance for the explicitly configured group's natural dialogue.

All actors, messages, prices and provider results are synthetic. No personal
opt-in or publication-confirmation ceremony is part of this lifecycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_worker import build_worker
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.services.coach_ai import ProviderResponse
from app_v2.services.coach_ingestor import ingest_coach_update


BOT, CHAT, TOPIC, OTHER_TOPIC, AUTHOR = 909090001, -999000113, 17, 18, 999000114


class NaturalProvider:
    def __init__(self):
        self.calls = []
        self.texts = ['Что помогло начать? Что хочешь попробовать дальше?']
        self.silent = False
        self.experiments = []
        self.fact_override = None

    def complete_json(self, **kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        self.calls.append(payload)
        source = payload['sources'][0] if payload['sources'] else None
        refs = [{key: source[key] for key in ('source_id', 'checksum')}] if source else []
        facts = [] if self.silent or source is None else [
            {'kind': 'action', 'quote': self.fact_override or source['text'][:100], **refs[0]}]
        result = dict(facts=facts, hypotheses=[], risk_flags=[], first_time_claim=False, recurring_claim=False)
        if 'messages' not in kwargs['schema']['properties']:
            result['search_terms'] = []
        else:
            result.update(action='SILENCE' if self.silent else 'RESPOND',
                          messages=[] if self.silent else [{'text': text, 'source_refs': refs} for text in self.texts],
                          experiments=[] if self.silent else [{**item, 'source_refs': refs} for item in self.experiments],
                          gaps=[])
        return ProviderResponse(json.dumps(result, ensure_ascii=False), 100, 80, 'synthetic-natural-model')


class NaturalSender:
    def __init__(self):
        self.attempts, self.sent = [], []
        self.fail_at = None

    def send(self, output):
        self.attempts.append(output)
        if self.fail_at == len(self.attempts):
            raise TimeoutError('synthetic ambiguous delivery')
        self.sent.append(output)
        return {'message_id': 5000 + len(self.attempts), 'chat': {'id': output.chat_id},
                'message_thread_id': output.message_thread_id}


@pytest.fixture
def natural_pilot(monkeypatch):
    url = os.getenv('NENOY_V2_TEST_DATABASE_URL')
    if not url:
        pytest.skip('NENOY_V2_TEST_DATABASE_URL is not configured')
    pg = pytest.importorskip('psycopg')
    name = 'coach_natural_' + uuid4().hex
    with pg.connect(url, autocommit=True) as admin:
        admin.execute(pg.sql.SQL('CREATE DATABASE {}').format(pg.sql.Identifier(name)))
        dedicated_url = pg.conninfo.make_conninfo(url, dbname=name)
        try:
            config = CoachConfig(
                environment='test', enabled=True, ai_enabled=True, database_url=dedicated_url,
                telegram_bot_token=f'{BOT}:synthetic_not_a_real_bot_token', webhook_secret='n' * 40,
                openai_api_key='synthetic-no-live-key', bot_user_id=BOT, bot_username='synthetic_natural_bot',
                chat_id=CHAT, topic_ids=(TOPIC, OTHER_TOPIC), archive_policy='until_author_deletes',
                ai_model='synthetic-natural-model', daily_cap_microusd=30_000_000,
                input_price_microusd_per_million=1_000_000, output_price_microusd_per_million=2_000_000,
                max_input_tokens=500_000, max_output_tokens=16000,
            )
            prepare(config)
            at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=10)

            class SyntheticClock:
                @staticmethod
                def now(tz=None):
                    return at + timedelta(hours=1)

            monkeypatch.setattr('app_v2.coach_worker.datetime', SyntheticClock)
            with pg.connect(dedicated_url, autocommit=True) as conn:
                provider, sender = NaturalProvider(), NaturalSender()
                workers = {topic: build_worker(conn, config, topic, sender=sender, provider=provider)
                           for topic in config.topic_ids}
                repo = CoachRepository(conn, chat_id=CHAT, message_thread_id=TOPIC, bot_user_id=BOT)
                counter = 0

                def update(text, *, author=AUTHOR, topic=TOPIC, chat=CHAT, **extra):
                    nonlocal counter
                    counter += 1
                    return {'update_id': counter, 'message': {
                        'message_id': counter, 'date': int((at + timedelta(seconds=counter)).timestamp()),
                        'from': {'id': author, 'is_bot': False},
                        'chat': {'id': chat, 'type': 'supergroup', 'is_forum': True},
                        'message_thread_id': topic, 'is_topic_message': True, 'text': text, **extra}}

                def drain(topic=TOPIC):
                    for _ in range(40):
                        if not workers[topic].run_once():
                            return
                    pytest.fail('synthetic natural dialogue did not drain')

                def intake(text, *, process=True, **kwargs):
                    event = update(text, **kwargs)
                    result = ingest_coach_update(event, conn=conn, config=config)
                    if process and kwargs.get('topic', TOPIC) in workers and kwargs.get('chat', CHAT) == CHAT:
                        drain(kwargs.get('topic', TOPIC))
                    return event, result

                yield SimpleNamespace(pg=pg, url=dedicated_url, config=config, conn=conn, at=at,
                                      repo=repo, workers=workers, provider=provider, sender=sender,
                                      update=update, intake=intake, drain=drain)
        finally:
            admin.execute(pg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(pg.sql.Identifier(name)))


def replies(pilot):
    return [item for item in pilot.sender.sent if item.mode != 'control']


def test_first_report_works_without_personal_optin_or_commands(natural_pilot):
    p = natural_pilot
    _, accepted = p.intake('Отчёт: закончил собственный синтетический черновик.')
    assert accepted.archived and accepted.queued, 'the first configured-group report must work immediately'
    assert p.provider.calls and replies(p)
    permission = p.repo.permissions(AUTHOR)
    assert permission.automatic_allowed and permission.direct_request_group_reply
    assert permission.track_status == 'active'
    assert not permission.disclosure_delivered, 'group policy must not fabricate delivery of an opt-in notice'
    row = p.conn.execute('SELECT grant_basis,actor_user_id FROM source_archive_consents WHERE author_user_id=%s', (AUTHOR,)).fetchone()
    assert row == ('group_default', None)
    assert p.conn.execute('SELECT grant_basis,actor_user_id FROM source_archive_consent_versions WHERE author_user_id=%s', (AUTHOR,)).fetchall() == [row]
    assert all('/' not in item.text and 'подтверд' not in item.text.casefold() for item in replies(p))


def test_natural_track_request_needs_no_confirmation_round_trip(natural_pilot):
    p = natural_pilot
    p.intake('Отчёт: сделал первый шаг к собственной учебной цели.')
    before = len(p.provider.calls)
    _, accepted = p.intake('Напарник, покажи мой трек за 7 дней')
    assert accepted.queued and len(p.provider.calls) > before
    assert any(call['mode'] == 'track' for call in p.provider.calls[before:])
    assert replies(p)[-1].mode == 'track'
    assert 'confirm' not in '\n'.join(item.text for item in p.sender.sent)
    assert 'подтверди' not in '\n'.join(item.text.casefold() for item in p.sender.sent)


def test_multiple_questions_messages_and_experiments_are_allowed(natural_pilot):
    p = natural_pilot
    proposals = [
        ('Напиши короткий черновик.', 'Заметь, стало ли проще начать.'),
        ('Сравни два собственных варианта.', 'Заметь, какой вариант понятнее.'),
    ]
    p.provider.texts = ['Что помогло начать? Что мешало продолжить?',
                        *[instruction + ' ' + observation for instruction, observation in proposals]]
    p.provider.experiments = [dict(instruction=instruction, observation=observation, minutes=3,
                                  reversible=True, voluntary=True, risk_level='low', message_index=index + 1)
                              for index, (instruction, observation) in enumerate(proposals)]
    p.intake('Отчёт: сравнил собственные способы начать задачу.')
    delivered = replies(p)
    assert [item.text for item in delivered] == p.provider.texts
    assert delivered[0].text.count('?') == 2
    assert len({item.bundle_id for item in delivered}) == 1
    assert [item.part_index for item in delivered] == [0, 1, 2]
    assert p.conn.execute("SELECT status FROM coach_episodes WHERE episode_type='experiment' ORDER BY id").fetchall() == [('offered',), ('offered',)]


def test_long_response_is_split_only_by_transport_size_without_losing_text(natural_pilot):
    p = natural_pilot
    original = 'Начало. ' + '🙂' * 5500 + ' Завершение.'
    p.provider.texts = [original]
    p.intake('Отчёт: проверил собственный план на синтетическом примере.')
    delivered = replies(p)
    assert len(delivered) >= 3
    assert ''.join(item.text for item in delivered) == original
    assert all(len(item.text.encode('utf-16-le')) // 2 <= 4096 for item in delivered)


def test_ordinary_chatter_can_be_silent_while_default_archive_is_kept(natural_pilot):
    p = natural_pilot
    p.provider.silent = True
    _, accepted = p.intake('Всем доброе утро!')
    assert accepted.archived and accepted.queued
    assert p.provider.calls and p.sender.sent == []
    assert p.conn.execute('SELECT available_text FROM source_archive_revisions').fetchone() == ('Всем доброе утро!',)


def test_natural_stop_persists_but_does_not_disable_direct_requests_or_other_authors(natural_pilot):
    p = natural_pilot
    p.intake('Отчёт: выполнил собственный синтетический шаг.')
    p.intake('Напарник, пока не комментируй')
    permission = p.repo.permissions(AUTHOR)
    assert not permission.auto_report_analysis and not permission.unsolicited_group_reply
    assert permission.track_status == 'paused' and permission.direct_request_group_reply
    before = len(p.provider.calls)
    _, accepted = p.intake('Отчёт: продолжаю работу самостоятельно.')
    assert accepted.archived and not accepted.queued and len(p.provider.calls) == before
    p.intake('Напарник, помоги выбрать следующий шаг')
    assert len(p.provider.calls) > before and replies(p)[-1].mode == 'direct'
    p.intake('Отчёт: другой синтетический автор сделал свой шаг.', author=AUTHOR + 1)
    assert p.repo.permissions(AUTHOR + 1).automatic_allowed
    assert not p.repo.permissions(AUTHOR).auto_report_analysis
    assert p.conn.execute('SELECT grant_basis,actor_user_id FROM source_archive_consents WHERE author_user_id=%s', (AUTHOR,)).fetchone() == ('self', AUTHOR)


def test_natural_storage_optout_excludes_previous_dialogue_from_current_only_reply(natural_pilot):
    p = natural_pilot
    p.provider.texts = ['ASSISTANT_PREVIOUS_PRIVATE_MARKER']
    p.intake('Отчёт: AUTHOR_PREVIOUS_PRIVATE_MARKER')
    p.intake('Напарник, не запоминай мои сообщения')
    permission = p.repo.permissions(AUTHOR)
    assert not permission.retain_source and not permission.historical_inference
    assert permission.direct_request_group_reply
    assert p.conn.execute('SELECT count(*) FROM coach_dialogue_turns WHERE text IS NOT NULL').fetchone()[0] == 0
    assert p.conn.execute("SELECT count(*) FROM coach_dialogue_turns WHERE source_proofs<>'[]'::jsonb").fetchone()[0] == 0
    p.provider.texts = ['Разберём только текущее обращение.']
    before = len(p.provider.calls)
    _, accepted = p.intake('Напарник, какой маленький шаг возможен сейчас?')
    assert accepted.queued and len(p.provider.calls) > before
    packets = p.provider.calls[before:]
    serialized = json.dumps(packets, ensure_ascii=False)
    assert 'ASSISTANT_PREVIOUS_PRIVATE_MARKER' not in serialized
    assert 'AUTHOR_PREVIOUS_PRIVATE_MARKER' not in serialized
    assert all(not packet.get('conversation') for packet in packets)


def test_natural_forget_is_not_undone_by_group_default_bootstrap(natural_pilot):
    p = natural_pilot
    p.intake('Отчёт: SYNTHETIC_DELETE_ME')
    p.intake('Напарник, забудь мои сообщения')
    permission = p.repo.permissions(AUTHOR)
    assert not permission.retain_source and not permission.historical_inference
    assert not permission.auto_report_analysis and not permission.unsolicited_group_reply
    assert p.conn.execute('SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL').fetchone()[0] == 0
    assert p.conn.execute('SELECT count(*) FROM coach_episodes WHERE summary IS NOT NULL').fetchone()[0] == 0
    with p.pg.connect(p.url, autocommit=True) as restarted:
        event = p.update('Отчёт: после удаления не разрешал возобновлять память.')
        accepted = ingest_coach_update(event, conn=restarted, config=p.config)
        assert not accepted.archived and not accepted.queued
    assert not p.repo.permissions(AUTHOR).retain_source


def test_natural_followup_receives_sent_bot_context_but_no_other_author_topic_or_group(natural_pilot):
    p = natural_pilot
    p.provider.texts = ['OWN_SENT_CONTEXT: какой из двух шагов выбираешь?']
    p.intake('Отчёт: OWN_AUTHOR_CONTEXT — сделал собственный черновик.')
    p.provider.texts = ['OTHER_AUTHOR_BOT_CONTEXT']
    p.intake('Отчёт: OTHER_AUTHOR_SOURCE_CONTEXT', author=AUTHOR + 1)
    p.provider.texts = ['OTHER_TOPIC_BOT_CONTEXT']
    p.intake('Отчёт: OTHER_TOPIC_SOURCE_CONTEXT', topic=OTHER_TOPIC)
    _, ignored = p.intake('OTHER_GROUP_NEVER_IMPORT', chat=CHAT - 1)
    assert ignored.status == 'ignored'
    p.provider.texts = ['Продолжим выбранный собственный шаг.']
    before = len(p.provider.calls)
    p.intake('Второй вариант могу сделать завтра.', reply_to_message={
        'message_id': 5001, 'from': {'id': BOT, 'is_bot': True}, 'text': 'FORGED_NESTED_REPLY_CONTEXT'})
    packets = p.provider.calls[before:]
    assert packets
    contexts = [turn for packet in packets for turn in packet.get('conversation', [])]
    assert any(turn['role'] == 'assistant' and 'OWN_SENT_CONTEXT' in turn['text'] for turn in contexts)
    assert any(turn['role'] == 'user' and 'OWN_AUTHOR_CONTEXT' in turn['text'] for turn in contexts)
    serialized = json.dumps(packets, ensure_ascii=False)
    for forbidden in ('OTHER_AUTHOR_BOT_CONTEXT', 'OTHER_AUTHOR_SOURCE_CONTEXT', 'OTHER_TOPIC_BOT_CONTEXT',
                      'OTHER_TOPIC_SOURCE_CONTEXT', 'OTHER_GROUP_NEVER_IMPORT', 'FORGED_NESTED_REPLY_CONTEXT'):
        assert forbidden not in serialized


@pytest.mark.parametrize('control', ['Напарник, пока не комментируй', 'Напарник, забудь мои сообщения'])
def test_natural_optout_after_first_bundle_part_cancels_remaining_parts(natural_pilot, control):
    p = natural_pilot
    p.provider.texts = ['Первая часть.', 'Вторая часть.', 'Третья часть.']
    p.intake('Отчёт: сделал собственный шаг перед синтетической остановкой.', process=False)
    assert p.workers[TOPIC].run_once()  # Generate the bundle.
    assert p.workers[TOPIC].run_once()  # Only its first part is sent.
    assert [item.text for item in replies(p)] == ['Первая часть.']
    p.intake(control)
    assert [item.text for item in replies(p)] == ['Первая часть.']
    assert p.conn.execute("SELECT count(*) FROM coaching_outputs WHERE mode<>'control' AND status IN ('pending','sending')").fetchone()[0] == 0


def test_ambiguous_middle_part_does_not_send_tail_or_retry_the_bundle(natural_pilot):
    p = natural_pilot
    p.provider.texts = ['Первая часть.', 'Неизвестный результат.', 'Хвост не отправлять.']
    p.sender.fail_at = 2
    p.intake('Отчёт: синтетическая проверка последовательного ответа.')
    p.drain()
    assert [item.text for item in p.sender.attempts] == p.provider.texts[:2]
    assert [item.text for item in replies(p)] == p.provider.texts[:1]
    assert p.conn.execute('SELECT status FROM coaching_outputs ORDER BY part_index').fetchall() == [('sent',), ('uncertain',), ('cancelled',)]


def test_report_mentioning_stop_is_not_mistaken_for_a_personal_optout(natural_pilot):
    p = natural_pilot
    p.provider.silent = True
    p.intake('Отчёт: решил больше не комментировать чужие задачи и заняться своей.')
    assert p.repo.permissions(AUTHOR).automatic_allowed
    assert p.repo.permissions(AUTHOR).track_status == 'active'


@pytest.mark.parametrize('control', [
    'Напарник, не используй историю',
    'Напарник, удали мой трек',
    'Напарник, забудь мои сообщения',
])
def test_personal_memory_controls_scrub_stored_assistant_context(natural_pilot, control):
    p = natural_pilot
    marker = 'ASSISTANT_CONTEXT_MUST_BE_PHYSICALLY_SCRUBBED'
    p.provider.texts = [marker]
    p.intake('Отчёт: собственный источник для удаления контекста.')
    assert p.conn.execute('SELECT text FROM coach_dialogue_turns').fetchall() == [(marker,)]
    p.intake(control)
    assert p.conn.execute('SELECT text,source_proofs FROM coach_dialogue_turns').fetchall() == [(None, [])]
    with p.pg.connect(p.url, autocommit=True) as restarted:
        repo = CoachRepository(restarted, chat_id=CHAT, message_thread_id=TOPIC, bot_user_id=BOT)
        context = repo.conversation_context(AUTHOR, before=p.at + timedelta(hours=1))
        assert not any(turn['role'] == 'assistant' and marker in turn['text'] for turn in context)


def test_editing_a_source_scrubs_its_prior_assistant_context(natural_pilot):
    p = natural_pilot
    p.provider.texts = ['ASSISTANT_BASED_ON_OLD_SOURCE']
    original, _ = p.intake('Отчёт: ORIGINAL_SOURCE_NO_LONGER_CURRENT')
    edited = p.update('Отчёт: EDITED_SOURCE_CURRENT')
    message = edited.pop('message')
    message.update(message_id=original['message']['message_id'], date=original['message']['date'],
                   edit_date=int((p.at + timedelta(seconds=30)).timestamp()))
    edited['edited_message'] = message
    result = ingest_coach_update(edited, conn=p.conn, config=p.config)
    assert result.archived and not result.queued
    assert p.conn.execute('SELECT text,source_proofs FROM coach_dialogue_turns').fetchall() == [(None, [])]
    p.provider.texts = ['Разберём актуальную версию.']
    before = len(p.provider.calls)
    p.intake('Напарник, продолжим с исправленной версией')
    packets = p.provider.calls[before:]
    assert packets
    serialized = json.dumps(packets, ensure_ascii=False)
    assert 'EDITED_SOURCE_CURRENT' in serialized
    assert 'ORIGINAL_SOURCE_NO_LONGER_CURRENT' not in serialized
    assert 'ASSISTANT_BASED_ON_OLD_SOURCE' not in serialized


def test_stopping_automatic_comments_preserves_direct_dialogue_with_current_proofs(natural_pilot):
    p = natural_pilot
    marker = 'ASSISTANT_CONTEXT_KEEP_FOR_DIRECT'
    p.provider.texts = [marker]
    p.intake('Отчёт: OWN_SOURCE_KEEP_FOR_DIRECT')
    previous = p.repo.permissions(AUTHOR).version
    p.intake('Напарник, пока не комментируй')
    current = p.repo.permissions(AUTHOR).version
    assert current > previous
    assert p.conn.execute('SELECT text,permission_version FROM coach_dialogue_turns').fetchall() == [(marker, previous)]
    context = p.repo.conversation_context(AUTHOR, before=p.at + timedelta(hours=1))
    old_assistant = [turn for turn in context if turn['role'] == 'assistant' and marker in turn['text']]
    assert old_assistant
    assert all(proof.access_version == current for turn in old_assistant for proof in turn['source_proofs'])
    p.provider.texts = ['Продолжим по твоему обращению.']
    before = len(p.provider.calls)
    _, accepted = p.intake('Напарник, помоги продолжить выбранный вариант')
    assert accepted.queued and replies(p)[-1].mode == 'direct'
    packets = p.provider.calls[before:]
    assert packets and all(packet['mode'] == 'direct' for packet in packets)
    assert any(marker in turn['text'] for packet in packets for turn in packet.get('conversation', []))
