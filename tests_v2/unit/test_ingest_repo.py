from __future__ import annotations

from app_v2.adapters.telegram_webhook import normalize_update
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
