from __future__ import annotations

import json

import pytest

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.domain.group_defaults import new_group_profile
from app_v2.group_admin import FRIENDS_DAY1_PROFILE
from app_v2.repositories.ingest_repo import TelegramIngestRepository


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query: str, params: tuple):
        self.calls.append((query, params))
        return _Result((1,))


def _group_update(text: str, update_id: int, *, reply_to_bot: bool = False) -> dict:
    message = {
        "message_id": update_id,
        "date": 1720000000,
        "from": {"id": 22, "first_name": "Серёга", "is_bot": False},
        "chat": {"id": -10055, "type": "supergroup", "title": "Друзья"},
        "text": text,
    }
    if reply_to_bot:
        message["reply_to_message"] = {
            "message_id": update_id - 1,
            "from": {"id": 999, "is_bot": True, "username": "nenoy"},
        }
    return {"update_id": update_id, "message": message}


def test_incomplete_direct_address_is_scheduled_with_short_delay() -> None:
    normalized = normalize_update(_group_update("НеНой, а ты можешь", 1001))
    assert normalized is not None
    conn = _Conn()
    inserted = TelegramIngestRepository(conn).insert_event(normalized)
    assert inserted is True
    query, params = conn.calls[-1]
    assert "INTERVAL '1 second'" in query
    assert params[-1] == 3.0


def test_complete_direct_address_is_immediately_available() -> None:
    normalized = normalize_update(_group_update("НеНой, можешь помочь с выбором", 1002))
    assert normalized is not None
    conn = _Conn()
    TelegramIngestRepository(conn).insert_event(normalized)
    _, params = conn.calls[-1]
    assert params[-1] == 0.0


def test_unfinished_reply_to_bot_is_also_debounced() -> None:
    normalized = normalize_update(_group_update("а ты можешь", 1003, reply_to_bot=True))
    assert normalized is not None
    conn = _Conn()
    TelegramIngestRepository(conn).insert_event(normalized)
    _, params = conn.calls[-1]
    assert params[-1] == 3.0


@pytest.mark.parametrize("telegram_type", ["group", "supergroup"])
def test_new_group_chat_is_auto_whitelisted_on_first_ingest(telegram_type) -> None:
    conn = _Conn()
    TelegramIngestRepository(conn).upsert_chat({"id": -10055, "type": telegram_type, "title": "Друзья"})
    query, params = conn.calls[-1]
    assert params[:4] == (-10055, "group", "Друзья", True)
    assert json.loads(params[4]) == FRIENDS_DAY1_PROFILE
    conflict_clause = query.split("ON CONFLICT", 1)[1]
    assert "is_whitelisted" not in conflict_clause
    assert "group_profile" not in conflict_clause
    assert "is_active" not in conflict_clause
    assert "silent_until" not in conflict_clause


@pytest.mark.parametrize("telegram_type", ["private", "channel"])
def test_non_group_chat_is_not_auto_whitelisted(telegram_type) -> None:
    conn = _Conn()
    TelegramIngestRepository(conn).upsert_chat({"id": 12345, "type": telegram_type, "first_name": "Fixture"})
    _, params = conn.calls[-1]
    assert params[3] is False
    assert json.loads(params[4]) == {}


def test_new_profile_has_no_shared_mutable_state() -> None:
    first = new_group_profile()
    first["initiative"] = 10
    assert new_group_profile() == FRIENDS_DAY1_PROFILE
