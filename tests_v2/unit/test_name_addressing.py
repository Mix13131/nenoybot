from __future__ import annotations

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.domain.enums import EventType


def _group_update(text: str, update_id: int = 900) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 77,
            "date": 1720000000,
            "from": {"id": 22, "first_name": "Серёга", "is_bot": False},
            "chat": {"id": -10055, "type": "supergroup", "title": "Друзья"},
            "text": text,
        },
    }


def test_group_can_address_bot_by_name_without_username() -> None:
    normalized = normalize_update(_group_update("НеНой, ты вообще здесь?"))

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.DIRECT_MENTION
    assert normalized.envelope.metadata["direct_mention"] is True
    assert normalized.envelope.metadata["name_address"] is True


def test_name_addressing_is_case_insensitive_and_supports_vocative_prefix() -> None:
    normalized = normalize_update(_group_update("Эй, неной бро, что скажешь?", 901))

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.DIRECT_MENTION
    assert normalized.envelope.metadata["name_address"] is True


def test_name_used_mid_sentence_is_not_forced_into_direct_address() -> None:
    normalized = normalize_update(_group_update("Этот НеНой вчера нормально ответил", 902))

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.GROUP_MESSAGE
    assert normalized.envelope.metadata["name_address"] is False


def test_common_phrase_ne_noi_is_not_bot_alias() -> None:
    normalized = normalize_update(_group_update("Не ной, всё нормально", 903))

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.GROUP_MESSAGE
    assert normalized.envelope.metadata["name_address"] is False


def test_bro_alone_is_not_bot_alias() -> None:
    normalized = normalize_update(_group_update("Бро, ты где?", 904))

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.GROUP_MESSAGE
    assert normalized.envelope.metadata["name_address"] is False
