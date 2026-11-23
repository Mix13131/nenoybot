"""History import crosses identity and transaction barriers without AI or sends."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from app_v2.adapters.coach_telegram import CoachTelegramClient, CoachTelegramError
from app_v2.adapters.telegram_history import HistoryExportError
from app_v2.coach_config import CoachConfig
import app_v2.services.coach_history_import as service


BOT = 990001
CHAT = -1000000007010
AUTHOR = 7101
OTHER_AUTHOR = 7102
TOPIC = 31
TOKEN = "990001:synthetic_token_never_real_123456"


def config(**changes):
    return replace(CoachConfig(
        environment="test", enabled=True, ai_enabled=True,
        database_url="postgresql://synthetic.invalid/coach_synthetic",
        telegram_bot_token=TOKEN, bot_user_id=BOT,
        bot_username="synthetic_history_bot", group_mode="joined_groups",
        archive_policy="until_author_deletes",
    ), **changes)


def payload(*authors):
    events = [{"id": TOPIC, "type": "service", "action": "topic_created",
               "date_unixtime": "1790000000", "title": "Synthetic topic"}]
    for index, author in enumerate(authors or (AUTHOR,), TOPIC + 1):
        events.append({"id": index, "type": "message", "from": "Synthetic author",
                       "from_id": f"user{author}", "date_unixtime": "1790000001",
                       "reply_to_message_id": TOPIC, "text": "Синтетический отчёт."})
    return json.dumps({"id": 7010, "type": "public_supergroup",
                       "name": "Synthetic group", "messages": events}).encode("utf-8")


class FakeTelegram:
    def __init__(self, *, identity=None, humans=None, group_error=None):
        self.identity = identity if identity is not None else {
            "id": BOT, "is_bot": True, "username": "synthetic_history_bot",
            "can_read_all_group_messages": True,
        }
        self.humans = humans or {}
        self.group_error = group_error
        self.calls = []
        self.closed = 0

    def get_me(self):
        self.calls.append(("identity",))
        return self.identity

    def check_group_access(self, **kwargs):
        self.calls.append(("group", kwargs))
        if self.group_error:
            raise self.group_error

    def history_author_is_human(self, *, chat_id, user_id):
        self.calls.append(("author", chat_id, user_id))
        result = self.humans.get(user_id, True)
        if isinstance(result, BaseException):
            raise result
        return result

    def send(self, *args, **kwargs):
        raise AssertionError("History import must not send Telegram messages")

    def close(self):
        self.closed += 1


@pytest.fixture
def persistence(monkeypatch):
    state = {"factory_calls": 0, "exports": [], "transaction_modes": [], "inside": False}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @contextmanager
        def transaction(self, *, force_rollback):
            state["transaction_modes"].append(force_rollback)
            state["inside"] = True
            try:
                yield
            finally:
                state["inside"] = False

    connection = Connection()

    def factory():
        state["factory_calls"] += 1
        return connection

    class Repository:
        def __init__(self, conn, *, bot_user_id):
            assert conn is connection and bot_user_id == BOT

        def import_export(self, export):
            assert state["inside"]
            state["exports"].append(export)
            return {"candidates": len(export.messages), "archived": len(export.messages),
                    "skipped_bot_author": export.skipped_counts.get("bot_author", 0),
                    "analysis_jobs_created": 0, "telegram_messages_sent": 0}

    monkeypatch.setattr(service, "SourceHistoryImportRepository", Repository)
    def forbidden_ai(*args, **kwargs):
        raise AssertionError("Import must not initialize or call an AI provider")
    monkeypatch.setattr("app_v2.services.coach_ai.OpenAIResponsesProvider", forbidden_ai)
    return state, factory, connection


@pytest.mark.parametrize("dry_run", [True, False])
def test_snapshot_import_uses_real_parser_and_correct_transaction_mode(persistence, dry_run):
    state, factory, _ = persistence
    telegram, body = FakeTelegram(), payload()
    result = service.import_history(config(), body, expected_chat_id=CHAT,
                                    dry_run=dry_run, client=telegram, connection_factory=factory)
    assert result == {
        "status": "preview" if dry_run else "imported", "dry_run": dry_run,
        "file_sha256": hashlib.sha256(body).hexdigest(),
        "counts": {"candidates": 1, "archived": 1, "skipped_bot_author": 0,
                   "analysis_jobs_created": 0, "telegram_messages_sent": 0},
    }
    assert state["factory_calls"] == 1 and state["transaction_modes"] == [dry_run]
    item = state["exports"][0].messages[0]
    assert (item.chat_id, item.message_thread_id, item.author_user_id) == (CHAT, TOPIC, AUTHOR)
    assert item.source_kind == "telegram_desktop_export"
    assert not hasattr(item, "telegram_update_id")
    assert telegram.closed == 0  # A caller-provided client remains caller-owned.
    assert telegram.calls[1] == ("group", {
        "chat_id": CHAT, "bot_user_id": BOT, "can_read_all_group_messages": True,
    })


@pytest.mark.parametrize("known_bot_in_export", [False, True])
def test_known_bot_messages_are_removed_before_persistence(persistence, known_bot_in_export):
    state, factory, _ = persistence
    telegram = FakeTelegram(humans={AUTHOR: True, OTHER_AUTHOR: False})
    data = json.loads(payload(AUTHOR, OTHER_AUTHOR, AUTHOR))
    if known_bot_in_export:
        data["messages"].append({**data["messages"][1], "id": TOPIC + 4,
                                 "from_id": f"user{BOT}", "is_bot": True})
    result = service.import_history(config(), json.dumps(data).encode(),
                                    expected_chat_id=CHAT, client=telegram, connection_factory=factory)
    export = state["exports"][0]
    assert len(export.messages) == 2
    assert {m.author_user_id for m in export.messages} == {AUTHOR}
    assert export.input_messages == 4 + int(known_bot_in_export)
    assert export.skipped_counts["bot_author"] == 1 + int(known_bot_in_export)
    assert result["counts"]["skipped_bot_author"] == 1 + int(known_bot_in_export)
    author_calls = [call for call in telegram.calls if call[0] == "author"]
    assert sorted(author_calls) == [("author", CHAT, AUTHOR), ("author", CHAT, OTHER_AUTHOR)]


def test_all_nonhuman_export_stops_before_database(persistence):
    state, factory, _ = persistence
    with pytest.raises(service.HistoryImportError, match="no_verified_human"):
        service.import_history(config(), payload(), expected_chat_id=CHAT,
                               client=FakeTelegram(humans={AUTHOR: False}), connection_factory=factory)
    assert state["factory_calls"] == 0 and state["exports"] == []


@pytest.mark.parametrize("change", [{"id": BOT + 1}, {"username": "different_synthetic_bot"}])
def test_wrong_bot_identity_stops_before_group_lookup_and_database(persistence, change):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    telegram.identity.update(change)
    with pytest.raises(service.HistoryImportError, match="history_bot_identity_mismatch"):
        service.import_history(config(), payload(), expected_chat_id=CHAT,
                               client=telegram, connection_factory=factory)
    assert telegram.calls == [("identity",)] and state["factory_calls"] == 0


@pytest.mark.parametrize("scope", [
    {"chat_id": CHAT, "topic_ids": (TOPIC + 1,)},
    {"chat_id": CHAT - 1, "topic_ids": (TOPIC,)},
])
def test_disallowed_configured_scope_stops_before_telegram_or_database(persistence, scope):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    with pytest.raises(service.HistoryImportError, match="history_scope_not_allowed"):
        service.import_history(config(group_mode="configured", **scope), payload(),
                               expected_chat_id=CHAT, client=telegram, connection_factory=factory)
    assert telegram.calls == [] and state["factory_calls"] == 0


def test_denied_group_membership_stops_before_author_lookup_and_database(persistence):
    state, factory, _ = persistence
    telegram = FakeTelegram(group_error=CoachTelegramError("bot_not_in_configured_group"))
    with pytest.raises(CoachTelegramError, match="bot_not_in_configured_group"):
        service.import_history(config(), payload(), expected_chat_id=CHAT,
                               client=telegram, connection_factory=factory)
    assert not any(call[0] == "author" for call in telegram.calls)
    assert state["factory_calls"] == 0


def test_partial_identity_lookup_failure_cannot_commit_earlier_verified_authors(persistence):
    state, factory, _ = persistence
    telegram = FakeTelegram(humans={AUTHOR: True,
        OTHER_AUTHOR: CoachTelegramError("history_author_identity_unverified")})
    with pytest.raises(CoachTelegramError, match="history_author_identity_unverified"):
        service.import_history(config(), payload(AUTHOR, OTHER_AUTHOR), expected_chat_id=CHAT,
                               client=telegram, connection_factory=factory)
    assert len([call for call in telegram.calls if call[0] == "author"]) == 2
    assert state["factory_calls"] == 0 and state["exports"] == []


@pytest.mark.parametrize("failure", [None, "identity", "author"])
def test_service_closes_only_its_own_telegram_client(monkeypatch, persistence, failure):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    if failure == "identity":
        telegram.identity["id"] = BOT + 1
    elif failure == "author":
        telegram.humans[AUTHOR] = CoachTelegramError("history_author_identity_unverified")
    created = []
    def make_client(token):
        created.append(token)
        return telegram
    monkeypatch.setattr(service, "CoachTelegramClient", make_client)
    if failure:
        with pytest.raises((service.HistoryImportError, CoachTelegramError)):
            service.import_history(config(), payload(), expected_chat_id=CHAT, connection_factory=factory)
        assert state["factory_calls"] == 0
    else:
        service.import_history(config(), payload(), expected_chat_id=CHAT, connection_factory=factory)
        assert state["factory_calls"] == 1
    assert created == [TOKEN] and telegram.closed == 1


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"database_url": None}, {"bot_user_id": None},
    {"telegram_bot_token": None}, {"archive_policy": None}, {"group_mode": "configured"},
])
def test_unconfigured_runtime_stops_before_telegram_or_database(persistence, change):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    with pytest.raises(service.HistoryImportError, match="history_runtime_not_configured"):
        service.import_history(config(**change), payload(), expected_chat_id=CHAT,
                               client=telegram, connection_factory=factory)
    assert telegram.calls == [] and state["factory_calls"] == 0


@pytest.mark.parametrize("arguments", [{"dry_run": "false"}, {"expected_chat_id": True},
                                       {"expected_chat_id": 123}, {"expected_chat_id": -(2**63)}])
def test_invalid_parameters_stop_before_any_external_access(persistence, arguments):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    with pytest.raises(service.HistoryImportError, match="invalid_history_import_parameters"):
        service.import_history(config(), payload(), **{"expected_chat_id": CHAT, **arguments},
                               client=telegram, connection_factory=factory)
    assert telegram.calls == [] and state["factory_calls"] == 0


def test_parser_errors_do_not_echo_private_payload_or_reach_telegram(persistence):
    state, factory, _ = persistence
    telegram = FakeTelegram()
    body = b'{"private-source-text":"private_credentials", bad-json}'
    with pytest.raises(HistoryExportError) as error:
        service.import_history(config(), body, expected_chat_id=CHAT,
                               client=telegram, connection_factory=factory)
    assert "private-source-text" not in str(error.value)
    assert "private_credentials" not in str(error.value)
    assert telegram.calls == [] and state["factory_calls"] == 0


def test_default_connection_is_bounded_and_preview_rolls_back(monkeypatch, persistence):
    state, _, connection = persistence
    connections = []
    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return connection
    monkeypatch.setattr(service, "connect", connect)
    current = config()
    result = service.import_history(current, payload(), expected_chat_id=CHAT, client=FakeTelegram())
    assert result["dry_run"] is True and state["transaction_modes"] == [True]
    assert connections == [(current.database_url, {
        "autocommit": True, "connect_timeout": 5,
        "options": "-c lock_timeout=5000 -c statement_timeout=15000",
    })]


@pytest.mark.parametrize("is_bot,expected", [(False, True), (True, False)])
def test_history_author_verification_reads_identity_without_writes(is_bot, expected):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {
            "status": "member", "user": {"id": AUTHOR, "is_bot": is_bot},
        }})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        actual = CoachTelegramClient(TOKEN, client=http).history_author_is_human(
            chat_id=CHAT, user_id=AUTHOR,
        )
    assert actual is expected and len(calls) == 1
    assert calls[0].url.path.endswith("/getChatMember")
    assert json.loads(calls[0].content) == {"chat_id": CHAT, "user_id": AUTHOR}


@pytest.mark.parametrize("member", [
    None, [], {}, {"user": {}}, {"user": {"id": AUTHOR}},
    {"user": {"id": str(AUTHOR), "is_bot": False}},
    {"user": {"id": AUTHOR + 1, "is_bot": False}},
    {"user": {"id": AUTHOR, "is_bot": 0}},
    {"user": {"id": AUTHOR, "is_bot": "false"}},
])
def test_unverified_author_identity_is_never_guessed(member, caplog):
    response = {"ok": True, "result": member}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as http:
        with pytest.raises(CoachTelegramError, match="^history_author_identity_unverified$"):
            CoachTelegramClient(TOKEN, client=http).history_author_is_human(chat_id=CHAT, user_id=AUTHOR)
    assert TOKEN not in caplog.text


def test_author_lookup_provider_failure_is_sanitized_before_database(persistence, caplog):
    state, factory, _ = persistence
    private = "private-source-text " + TOKEN
    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        data = json.loads(request.content)
        if method == "getMe":
            value = {"id": BOT, "is_bot": True, "username": "synthetic_history_bot",
                     "can_read_all_group_messages": True}
        elif method == "getChat":
            value = {"id": CHAT, "type": "supergroup"}
        elif method == "getChatMember" and data["user_id"] == BOT:
            value = {"status": "member", "user": {"id": BOT, "is_bot": True}}
        else:
            return httpx.Response(400, json={"ok": False, "description": private})
        return httpx.Response(200, json={"ok": True, "result": value})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        telegram = CoachTelegramClient(TOKEN, client=http)
        with pytest.raises(CoachTelegramError, match="^telegram_http_error$") as error:
            service.import_history(config(), payload(), expected_chat_id=CHAT,
                                   client=telegram, connection_factory=factory)
    assert private not in str(error.value) + caplog.text
    assert state["factory_calls"] == 0 and state["exports"] == []
