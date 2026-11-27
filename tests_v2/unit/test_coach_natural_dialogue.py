"""Independent product checks for default-on, unrestricted natural replies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from app_v2.coach_config import CoachConfigurationError, load_coach_config
from app_v2.domain.coach import CoachJob, CoachPermissions, SourceProof
from app_v2.repositories.source_archive_repo import ArchiveSource
from app_v2.services.coach_engine import CoachEngine, CoachRequest
from app_v2.services.coach_sources import CoachSourceSelection


NOW = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)
CHAT, TOPIC, AUTHOR = -999000313, 17, 999000314


def configured_environment():
    return {
        'COACH_ENV': 'test', 'COACH_DATABASE_URL': 'postgresql://synthetic.invalid/do-not-connect',
        'COACH_TELEGRAM_BOT_TOKEN': '909090001:synthetic_not_a_real_bot_token',
        'COACH_BOT_USERNAME': 'synthetic_natural_bot', 'COACH_WEBHOOK_SECRET': 's' * 40,
        'COACH_CHAT_ID': str(CHAT), 'COACH_TOPIC_IDS': str(TOPIC),
        'COACH_PUBLIC_BASE_URL': 'https://synthetic.invalid', 'COACH_ARCHIVE_POLICY': 'until_author_deletes',
        'COACH_OPENAI_API_KEY': 'synthetic-no-live-key', 'COACH_AI_MODEL': 'synthetic-natural-model',
        'COACH_AI_EMERGENCY_DAILY_USD': '30', 'COACH_AI_INPUT_USD_PER_MILLION': '1',
        'COACH_AI_OUTPUT_USD_PER_MILLION': '2', 'COACH_AI_MAX_INPUT_TOKENS': '500000',
        'COACH_AI_MAX_OUTPUT_TOKENS': '16000',
    }


def test_fully_configured_group_is_active_without_enable_flags():
    config = load_coach_config(configured_environment())
    assert config.enabled and config.ai_enabled


def test_absent_configuration_remains_inactive_and_explicit_kill_switches_work():
    empty = load_coach_config({})
    assert not empty.enabled and not empty.ai_enabled
    stopped = load_coach_config({**configured_environment(), 'COACH_ENABLED': '0', 'COACH_AI_ENABLED': '0'})
    assert not stopped.enabled and not stopped.ai_enabled


def test_default_active_group_still_requires_an_explicit_ai_tariff():
    env = configured_environment()
    del env['COACH_AI_INPUT_USD_PER_MILLION']
    with pytest.raises(CoachConfigurationError, match='COACH_AI_INPUT_USD_PER_MILLION'):
        load_coach_config(env)


def own_source():
    body = 'Я сделал собственный синтетический черновик.'
    return ArchiveSource(
        source_id=f'tgsrc:{CHAT}:{TOPIC}:101:201', chat_id=CHAT, message_thread_id=TOPIC,
        author_user_id=AUTHOR, telegram_message_id=101, telegram_update_id=201,
        message_revision=1, occurred_at=NOW - timedelta(hours=1), revision_at=NOW - timedelta(hours=1),
        captured_at=NOW - timedelta(hours=1), is_edited=False, reply_to_message_id=None,
        content_kind='text', available_text=body, body_coverage='full', media_reference={},
        contains_external_content=False, consent_version=1, checksum=hashlib.sha256(body.encode()).hexdigest(),
        access_version=1,
    )


class SourceState:
    chat_id, message_thread_id = CHAT, TOPIC

    def __init__(self):
        self.saved, self.offered, self.conversation = [], [], []

    def scope_enabled(self):
        return True

    def permissions(self, author):
        assert author == AUTHOR
        return CoachPermissions(version=1, retain_source=True, historical_inference=True,
                                auto_report_analysis=True, unsolicited_group_reply=True,
                                direct_request_group_reply=True, track_status='active',
                                disclosure_delivered=False, grant_basis='group_default')

    def save_episode(self, **kwargs):
        self.saved.append(kwargs)
        return 'a' * 32

    def save_experiment(self, **kwargs):
        self.offered.append(kwargs)
        return f'{len(self.offered):032x}'

    def list_episodes(self, **kwargs):
        return []

    def list_experiments(self, **kwargs):
        return []

    def conversation_context(self, author, **kwargs):
        assert author == AUTHOR
        return self.conversation


class SelectedSource:
    def __init__(self, source):
        self.source = source

    def current(self, **kwargs):
        return self.source if kwargs['source_id'] == self.source.source_id else None

    def select(self, **kwargs):
        return CoachSourceSelection((self.source,), 1, 1, 1, 0, kwargs['since'], kwargs['until'], 'synthetic')


class AuthorizedArchive:
    def reauthorize(self, sources, **scope):
        return all((source.chat_id, source.message_thread_id, source.author_user_id) ==
                   (scope['chat_id'], scope['message_thread_id'], scope['author_user_id']) for source in sources)


class NaturalAI:
    def __init__(self, texts, *, silent=False, experiments=(), assistant_fact=None):
        self.texts, self.silent, self.experiments, self.assistant_fact = texts, silent, experiments, assistant_fact
        self.calls = []

    def request_json(self, **kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        self.calls.append(payload)
        first = payload['sources'][0]
        ref = {key: first[key] for key in ('source_id', 'checksum')}
        result = dict(facts=[{'kind': 'action', 'quote': first['text'][:100], **ref}],
                      hypotheses=[], risk_flags=[], first_time_claim=False, recurring_claim=False)
        if 'messages' not in kwargs['schema']['properties']:
            result['search_terms'] = []
        else:
            if self.assistant_fact:
                result['facts'][0]['quote'] = self.assistant_fact
            result.update(action='SILENCE' if self.silent else 'RESPOND',
                          messages=[] if self.silent else [{'text': text, 'source_refs': [ref]} for text in self.texts],
                          experiments=[{**experiment, 'source_refs': [ref]} for experiment in self.experiments], gaps=[])
        return SimpleNamespace(parsed=result)


def engine_case(ai, *, mode='auto_report'):
    source, state = own_source(), SourceState()
    job = CoachJob(id='natural-synthetic-job', chat_id=CHAT, message_thread_id=TOPIC,
                   author_user_id=AUTHOR, telegram_update_id=201, telegram_message_id=101,
                   mode=mode, current_text=source.available_text, source_checksum=source.checksum,
                   source_id=source.source_id, consent_version=1, lease_token='natural-synthetic-lease',
                   occurred_at=source.occurred_at)
    request = CoachRequest(operation_id='natural-synthetic-operation', mode=mode, chat_id=CHAT,
                           message_thread_id=TOPIC, author_user_id=AUTHOR, current_text=source.available_text,
                           source_id=source.source_id, source_checksum=source.checksum, consent_version=1,
                           now=NOW, public_confirmed=False, originating_job=job, current_guard=lambda: True)
    engine = CoachEngine(archive=AuthorizedArchive(), state=state, ai=ai, selector=SelectedSource(source))
    return engine, request, state, source


@pytest.mark.parametrize('mode', ['auto_report', 'direct'])
def test_natural_response_accepts_several_questions_and_message_parts(mode):
    texts = ['Что помогло начать? Что помешало продолжить?',
             'Разберём оба наблюдения.', 'Какой следующий шаг ты выбираешь?']
    ai = NaturalAI(texts)
    engine, request, state, _ = engine_case(ai, mode=mode)
    decision = engine.process(request)
    assert decision.action == 'RESPOND' and decision.messages == tuple(texts)
    assert state.saved and ai.calls


def test_more_than_one_experiment_is_linked_to_its_actual_message():
    proposals = [('Сделай короткий черновик.', 'Заметь, стало ли проще начать.'),
                 ('Сравни два собственных варианта.', 'Заметь, какой понятнее.')]
    texts = [instruction + ' ' + observation for instruction, observation in proposals]
    experiments = [dict(instruction=instruction, observation=observation, minutes=3, reversible=True,
                        voluntary=True, risk_level='low', message_index=index)
                   for index, (instruction, observation) in enumerate(proposals)]
    engine, request, state, _ = engine_case(NaturalAI(texts, experiments=experiments))
    decision = engine.process(request)
    assert decision.action == 'RESPOND' and decision.messages == tuple(texts)
    assert decision.experiment_links == (('0' * 31 + '1', 0), ('0' * 31 + '2', 1))
    assert len(state.offered) == 2


@pytest.mark.parametrize('mode', ['track', 'weekly_review'])
def test_requested_review_does_not_require_a_public_confirmation_gate(mode):
    ai = NaturalAI(['По собственным источникам ты сделал первый шаг.'])
    engine, request, _, _ = engine_case(ai, mode=mode)
    decision = engine.process(request)
    assert request.public_confirmed is False
    assert decision.action == 'RESPOND' and decision.messages
    assert ai.calls and all('confirm' not in text for text in decision.messages)


def test_normal_contextual_silence_is_still_a_valid_outcome():
    ai = NaturalAI([], silent=True)
    engine, request, _, _ = engine_case(ai)
    decision = engine.process(request)
    assert decision.action == 'SILENCE' and not decision.messages
    assert len(ai.calls) == 2, 'the final decision must still see the authorized context'


def test_prior_assistant_reply_is_context_not_a_user_fact():
    assistant_text = 'SYNTHETIC_ASSISTANT_SUGGESTION_NOT_A_USER_ACTION'
    ai = NaturalAI(['Продолжим разговор.'], assistant_fact=assistant_text)
    engine, request, state, source = engine_case(ai)
    state.conversation = [dict(role='assistant', text=assistant_text, telegram_message_id=5001,
                               occurred_at=NOW - timedelta(minutes=1),
                               source_proofs=[SourceProof(source.source_id, source.checksum, 1)])]
    decision = engine.process(request)
    assert any(assistant_text in json.dumps(call.get('conversation', [])) for call in ai.calls)
    assert decision.action == 'SILENCE' and not decision.messages
    assert all(assistant_text not in row['summary'] for row in state.saved)
