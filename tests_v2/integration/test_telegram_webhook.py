from __future__ import annotations

from contextlib import nullcontext

from fastapi.testclient import TestClient

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.config import AppConfig
from app_v2.domain.enums import EventType, ScopeType
from app_v2.services.event_ingestor import IngestResult, ingest_telegram_update


def _private_update(update_id: int = 100) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 7,
            "date": 1720000000,
            "from": {"id": 11, "first_name": "Антон", "is_bot": False},
            "chat": {"id": 11, "type": "private", "first_name": "Антон"},
            "text": "Привет",
        },
    }


def test_normalize_private_message() -> None:
    normalized = normalize_update(_private_update())
    assert normalized is not None
    assert normalized.envelope.event_type is EventType.PRIVATE_MESSAGE
    assert normalized.envelope.scope_type is ScopeType.PERSONAL
    assert normalized.envelope.scope_id == "11"
    assert normalized.envelope.actor_user_id == "11"


def test_normalize_group_reply_to_bot_and_mentions() -> None:
    update = {
        "update_id": 101,
        "message": {
            "message_id": 8,
            "date": 1720000001,
            "from": {"id": 22, "first_name": "Серёга", "is_bot": False},
            "chat": {"id": -10055, "type": "supergroup", "title": "Друзья"},
            "text": "@nenoy ну ты даёшь",
            "entities": [{"type": "mention", "offset": 0, "length": 6}],
            "reply_to_message": {
                "message_id": 6,
                "from": {"id": 999, "is_bot": True, "username": "nenoy"},
            },
        },
    }
    normalized = normalize_update(update, bot_username="nenoy")
    assert normalized is not None
    assert normalized.envelope.event_type is EventType.REPLY_TO_BOT
    assert normalized.envelope.scope_type is ScopeType.GROUP
    assert normalized.envelope.metadata["reply_to_bot"] is True
    assert normalized.envelope.metadata["direct_mention"] is True
    assert normalized.envelope.metadata["mentions"][0]["type"] == "mention"


def test_normalize_configured_group_mention_as_direct_mention() -> None:
    update = {
        "update_id": 106,
        "message": {
            "message_id": 9,
            "date": 1720000002,
            "from": {"id": 22, "first_name": "Серёга", "is_bot": False},
            "chat": {"id": -10055, "type": "supergroup", "title": "Друзья"},
            "text": "@NeNoy а ты что думаешь?",
            "entities": [{"type": "mention", "offset": 0, "length": 6}],
        },
    }
    normalized = normalize_update(update, bot_username="nenoy")
    assert normalized is not None
    assert normalized.envelope.event_type is EventType.DIRECT_MENTION
    assert normalized.envelope.metadata["direct_mention"] is True


def test_normalize_text_mention_by_configured_bot_user_id() -> None:
    update = {
        "update_id": 107,
        "message": {
            "message_id": 10,
            "date": 1720000003,
            "from": {"id": 22, "first_name": "Серёга", "is_bot": False},
            "chat": {"id": -10055, "type": "group", "title": "Друзья"},
            "text": "НеНой, ответь",
            "entities": [{
                "type": "text_mention", "offset": 0, "length": 5,
                "user": {"id": 999, "is_bot": True, "username": "nenoy"},
            }],
        },
    }
    normalized = normalize_update(update, bot_user_id="999")
    assert normalized is not None
    assert normalized.envelope.event_type is EventType.DIRECT_MENTION


def test_normalize_edited_message() -> None:
    update = _private_update(102)
    update["edited_message"] = update.pop("message")
    update["edited_message"]["edit_date"] = 1720000100
    normalized = normalize_update(update)
    assert normalized is not None
    assert normalized.envelope.event_type is EventType.EDITED_MESSAGE
    assert normalized.envelope.metadata["edited"] is True


def test_normalize_reaction_added_and_removed() -> None:
    base = {
        "chat": {"id": -10055, "type": "group", "title": "Друзья"},
        "message_id": 8,
        "user": {"id": 22, "first_name": "Серёга"},
        "date": 1720000200,
        "old_reaction": [],
    }
    added = normalize_update({"update_id": 103, "message_reaction": {**base, "new_reaction": [{"type": "emoji", "emoji": "😂"}]}})
    removed = normalize_update({"update_id": 104, "message_reaction": {**base, "old_reaction": [{"type": "emoji", "emoji": "😂"}], "new_reaction": []}})
    assert added is not None and added.envelope.event_type is EventType.REACTION_ADDED
    assert removed is not None and removed.envelope.event_type is EventType.REACTION_REMOVED


def test_unknown_update_is_ignored() -> None:
    assert normalize_update({"update_id": 105, "poll": {"id": "x"}}) is None


def test_webhook_secret_and_unknown_update(monkeypatch) -> None:
    import app_v2.main as main_module

    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(environment="test", app_name="НеНой 2.0", webhook_secret="secret-123"),
    )
    client = TestClient(main_module.app)

    denied = client.post("/webhooks/telegram", json={"update_id": 200, "poll": {"id": "x"}})
    assert denied.status_code == 401

    accepted = client.post(
        "/webhooks/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
        json={"update_id": 200, "poll": {"id": "x"}},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ignored"


def test_webhook_delegates_supported_update_without_llm(monkeypatch) -> None:
    import app_v2.main as main_module

    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(
            environment="test",
            app_name="НеНой 2.0",
            webhook_secret=None,
            telegram_bot_username="nenoy",
            telegram_bot_user_id="999",
        ),
    )
    seen: list[tuple[dict, dict]] = []

    def fake_ingest(update: dict, **kwargs) -> IngestResult:
        seen.append((update, kwargs))
        return IngestResult(status="accepted", event_id="tg:300")

    monkeypatch.setattr(main_module, "ingest_telegram_update", fake_ingest)
    client = TestClient(main_module.app)
    response = client.post("/webhooks/telegram", json=_private_update(300))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "event_id": "tg:300"}
    assert seen[0][0]["update_id"] == 300
    assert seen[0][1] == {"bot_username": "nenoy", "bot_user_id": "999"}


def test_ingestor_duplicate_is_idempotent(monkeypatch) -> None:
    import app_v2.services.event_ingestor as ingestor_module

    known_updates: set[int] = set()

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def transaction(self):
            return nullcontext()

    class FakeRepo:
        def __init__(self, conn):
            pass

        def event_exists(self, update_id: int) -> bool:
            return update_id in known_updates

        def upsert_user(self, user):
            return 1

        def upsert_chat(self, chat):
            return 1

        def upsert_member(self, chat_id, user_id):
            return None

        def store_message(self, normalized, chat_id, user_id):
            return None

        def insert_event(self, normalized) -> bool:
            known_updates.add(normalized.telegram_update_id)
            return True

    monkeypatch.setattr(ingestor_module, "connect", lambda database_url=None: FakeConn())
    monkeypatch.setattr(ingestor_module, "TelegramIngestRepository", FakeRepo)

    first = ingest_telegram_update(_private_update(400), "postgresql://fake")
    second = ingest_telegram_update(_private_update(400), "postgresql://fake")

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert first.event_id == second.event_id == "tg:400"
