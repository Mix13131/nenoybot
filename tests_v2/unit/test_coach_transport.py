"""No live HTTP: actual httpx/FastAPI boundaries use synthetic transports."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app_v2.adapters.coach_telegram import CoachTelegramClient, CoachTelegramError
from app_v2.coach_config import CoachConfig


TOKEN = '990001:synthetic_token_never_real_123456'
SECRET = 'synthetic_webhook_secret_1234567890'
CHAT, TOPIC, BOT = -990001, 17, 990001


def output(**changes):
    values = dict(chat_id=CHAT, message_thread_id=TOPIC, text='Синтетический ответ', request_message_id=123)
    values.update(changes)
    return SimpleNamespace(**values)


def config(**changes):
    return replace(CoachConfig(environment='test', enabled=True, webhook_secret=SECRET,
        bot_user_id=BOT, bot_username='synthetic_coach_bot', chat_id=CHAT,
        topic_ids=(TOPIC,), archive_policy='until_author_deletes'), **changes)


def result(**changes):
    values = {'message_id': 901, 'chat': {'id': CHAT}, 'message_thread_id': TOPIC}
    values.update(changes)
    return {'ok': True, 'result': values}


def test_telegram_send_uses_exact_topic_reply_and_no_parse_mode_or_previews(caplog):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=result())
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = CoachTelegramClient(TOKEN, client=http)
        sent = client.send(output())
    assert sent['message_id'] == 901 and len(calls) == 1
    assert calls[0].method == 'POST' and calls[0].url.path.endswith('/sendMessage')
    payload = json.loads(calls[0].content)
    assert payload == {'chat_id': CHAT, 'message_thread_id': TOPIC, 'text': 'Синтетический ответ',
        'link_preview_options': {'is_disabled': True}, 'reply_parameters': {'message_id': 123}}
    assert TOKEN not in caplog.text and 'Синтетический ответ' not in caplog.text


@pytest.mark.parametrize('changes', [
    {'chat_id': 123}, {'chat_id': True}, {'message_thread_id': 0}, {'message_thread_id': True},
    {'text': ''}, {'text': 'x' * 4097}, {'text': '🙂' * 2049}, {'request_message_id': -1},
    {'request_message_id': True}, {'request_message_id': '123'},
])
def test_invalid_delivery_parameters_never_reach_http(changes):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=result())
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError):
            CoachTelegramClient(TOKEN, client=http).send(output(**changes))
    assert calls == []


def test_telegram_setup_does_not_present_a_command_driven_interface():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True, 'result': True})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        CoachTelegramClient(TOKEN, client=http).configure_commands()
    assert len(calls) == 1 and calls[0].url.path.endswith('/setMyCommands')
    assert json.loads(calls[0].content) == {'commands': []}


@pytest.mark.parametrize('response', [
    httpx.Response(500, text='PRIVATE_PROVIDER_DETAILS'),
    httpx.Response(200, json={'ok': False, 'description': 'PRIVATE_PROVIDER_DETAILS'}),
    httpx.Response(200, content=b'not-json PRIVATE_PROVIDER_DETAILS'),
    httpx.Response(200, json={'ok': True, 'result': True}),
])
def test_provider_failures_are_sanitized_and_never_retried(response, caplog):
    calls = []
    def handle(request):
        calls.append(request)
        return response
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError) as error:
            CoachTelegramClient(TOKEN, client=http).send(output())
    assert len(calls) == 1
    assert TOKEN not in str(error.value) + caplog.text
    assert 'PRIVATE_PROVIDER_DETAILS' not in str(error.value) + caplog.text


def test_timeout_has_unknown_result_with_no_retry_or_token_bearing_exception_chain():
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout('PRIVATE ' + TOKEN, request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError) as error:
            CoachTelegramClient(TOKEN, client=http).send(output())
    assert len(calls) == 1 and str(error.value) == 'telegram_result_unknown'
    assert error.value.__suppress_context__ is True
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('reply', [result(chat={'id': CHAT - 1}), result(message_thread_id=TOPIC + 1)])
def test_mismatched_delivery_receipt_is_not_confirmed(reply):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply))) as http:
        with pytest.raises(CoachTelegramError):
            CoachTelegramClient(TOKEN, client=http).send(output())


@pytest.mark.parametrize('already_configured', [False, True])
def test_webhook_registration_preserves_pending_updates_and_checks_url(already_configured):
    calls = []
    expected = 'https://synthetic.invalid/webhooks/telegram'
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            response = {'url': expected if already_configured else ''}
        else:
            response = True if request.url.path.endswith('/setWebhook') else {'url': expected, 'pending_update_count': 3}
        return httpx.Response(200, json={'ok': True, 'result': response})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        receipt = CoachTelegramClient(TOKEN, client=http).configure_webhook(public_base_url='https://synthetic.invalid/', secret=SECRET)
    assert [call.url.path.rsplit('/', 1)[-1] for call in calls] == ['getWebhookInfo', 'setWebhook', 'getWebhookInfo']
    assert receipt['verified'] is True and receipt['pending_update_count'] == 3
    body = json.loads(calls[1].content)
    assert body['url'] == expected and body['secret_token'] == SECRET
    assert body['drop_pending_updates'] is False
    assert body['allowed_updates'] == ['message', 'edited_message']


def test_wrong_webhook_url_is_not_reported_verified():
    calls = []
    def handle(request):
        calls.append(request)
        value = ({'url': ''} if len(calls) == 1 else True if request.url.path.endswith('/setWebhook')
                 else {'url': 'https://other.invalid/webhooks/telegram'})
        return httpx.Response(200, json={'ok': True, 'result': value})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError, match='not_verified'):
            CoachTelegramClient(TOKEN, client=http).configure_webhook(public_base_url='https://synthetic.invalid', secret=SECRET)
    assert [call.url.path.rsplit('/', 1)[-1] for call in calls] == ['getWebhookInfo', 'setWebhook', 'getWebhookInfo']


def test_another_deployments_webhook_is_never_overwritten():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'url': 'https://synthetic-existing.invalid/webhook'}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError, match='belongs_to_another_deployment'):
            CoachTelegramClient(TOKEN, client=http).configure_webhook(public_base_url='https://synthetic.invalid', secret=SECRET)
    assert [call.url.path.rsplit('/', 1)[-1] for call in calls] == ['getWebhookInfo']


@pytest.mark.parametrize('existing', [None, {}, {'url': None}])
def test_unknown_existing_webhook_state_never_mutates_telegram(existing):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True, 'result': existing})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(CoachTelegramError, match='existing_webhook_unknown'):
            CoachTelegramClient(TOKEN, client=http).configure_webhook(public_base_url='https://synthetic.invalid', secret=SECRET)
    assert [call.url.path.rsplit('/', 1)[-1] for call in calls] == ['getWebhookInfo']


@pytest.mark.parametrize('status,can_read,accepted', [
    ('member', True, True), ('administrator', False, True), ('member', False, False), ('left', True, False),
])
def test_group_privacy_mode_access_is_verified(status, can_read, accepted):
    def handle(request):
        value = {'id': CHAT, 'type': 'supergroup', 'is_forum': True} if request.url.path.endswith('/getChat') else {'status': status}
        return httpx.Response(200, json={'ok': True, 'result': value})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = CoachTelegramClient(TOKEN, client=http)
        if accepted:
            client.check_group_access(chat_id=CHAT, bot_user_id=BOT, can_read_all_group_messages=can_read)
        else:
            with pytest.raises(CoachTelegramError):
                client.check_group_access(chat_id=CHAT, bot_user_id=BOT, can_read_all_group_messages=can_read)


def never_connect():
    raise AssertionError('No database connection is allowed for this request')


def test_unauthenticated_request_does_not_even_read_the_body():
    from app_v2.coach_main import create_app
    app = create_app(config(), connection_factory=never_connect)
    sent = []
    async def receive():
        raise AssertionError('An unauthenticated body must not be read')
    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'POST', 'scheme': 'http', 'path': '/webhooks/telegram', 'raw_path': b'/webhooks/telegram',
        'query_string': b'', 'root_path': '', 'headers': [], 'server': ('testserver', 80), 'client': ('testclient', 123)}
    asyncio.run(app(scope, receive, send))
    assert next(item['status'] for item in sent if item['type'] == 'http.response.start') == 401


def test_non_ascii_secret_header_is_unauthorized_not_an_internal_error():
    from app_v2.coach_main import create_app
    client = TestClient(create_app(config(), connection_factory=never_connect))
    response = client.post('/webhooks/telegram', headers=[(b'X-Telegram-Bot-Api-Secret-Token', b'\xff')], content=b'{}')
    assert response.status_code == 401


@pytest.mark.parametrize('body,status', [(b'not json', 400), (b'[]', 400), (b'\xff', 400), (b'x' * (1024 * 1024 + 1), 413)])
def test_invalid_or_oversized_webhook_body_never_opens_database(body, status):
    from app_v2.coach_main import create_app
    client = TestClient(create_app(config(), connection_factory=never_connect))
    response = client.post('/webhooks/telegram', headers={'X-Telegram-Bot-Api-Secret-Token': SECRET}, content=body)
    assert response.status_code == status


class FakeConnection:
    def __init__(self):
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.closed = True


def test_ingest_failure_returns_retry_without_logging_private_payload(monkeypatch, caplog):
    import app_v2.coach_main as module
    def failed(*args, **kwargs):
        raise RuntimeError('PRIVATE_PARTICIPANT_TEXT ' + TOKEN)
    monkeypatch.setattr(module, 'ingest_coach_update', failed)
    connection = FakeConnection()
    client = TestClient(module.create_app(config(), connection_factory=lambda: connection))
    response = client.post('/webhooks/telegram', headers={'X-Telegram-Bot-Api-Secret-Token': SECRET}, json={'update_id': 1})
    assert response.status_code == 503 and connection.closed
    assert 'retry' in response.text.lower()
    assert 'PRIVATE_PARTICIPANT_TEXT' not in response.text + caplog.text
    assert TOKEN not in response.text + caplog.text


def test_ack_is_returned_after_connection_context_exits(monkeypatch):
    import app_v2.coach_main as module
    from app_v2.services.coach_ingestor import CoachIngestResult
    connection = FakeConnection()
    def accepted(update, *, conn, config):
        assert conn is connection and not connection.closed
        return CoachIngestResult('accepted', archived=True)
    monkeypatch.setattr(module, 'ingest_coach_update', accepted)
    client = TestClient(module.create_app(config(), connection_factory=lambda: connection))
    response = client.post('/webhooks/telegram', headers={'X-Telegram-Bot-Api-Secret-Token': SECRET}, json={'update_id': 1})
    assert response.status_code == 200 and response.json() == {'status': 'accepted'}
    assert connection.closed


@pytest.mark.parametrize('variant', ['group', 'topic', 'private', 'anonymous', 'not_forum'])
def test_wrong_source_scope_does_not_enter_repository(variant, monkeypatch):
    import app_v2.services.coach_ingestor as module
    message = {'message_id': 1, 'date': 1793491200, 'text': 'synthetic',
        'from': {'id': 990101, 'is_bot': False}, 'chat': {'id': CHAT, 'type': 'supergroup', 'is_forum': True},
        'message_thread_id': TOPIC, 'is_topic_message': True}
    if variant == 'group':
        message['chat']['id'] = CHAT - 1
    elif variant == 'topic':
        message['message_thread_id'] = TOPIC + 1
    elif variant == 'private':
        message['chat']['type'] = 'private'
    elif variant == 'anonymous':
        message['sender_chat'] = {'id': CHAT}
    else:
        message['chat']['is_forum'] = False
    monkeypatch.setattr(module, 'CoachRepository', lambda *a, **k: never_connect())
    assert module.ingest_coach_update({'update_id': 1, 'message': message}, conn=None, config=config()).status == 'ignored'
