from __future__ import annotations

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.domain.enums import EventType


def test_actor_chat_reaction_preserves_stable_identity_metadata() -> None:
    normalized = normalize_update(
        {
            "update_id": 99001,
            "message_reaction": {
                "chat": {"id": -100555, "type": "supergroup", "title": "Synthetic"},
                "message_id": 77,
                "date": 1720000000,
                "actor_chat": {
                    "id": -100777,
                    "type": "supergroup",
                    "title": "Anonymous Admin",
                    "username": "synthetic_actor",
                },
                "old_reaction": [],
                "new_reaction": [{"type": "emoji", "emoji": "👍"}],
            },
        }
    )

    assert normalized is not None
    assert normalized.envelope.event_type is EventType.REACTION_ADDED
    assert normalized.envelope.actor_user_id is None
    assert normalized.telegram_user is None
    assert normalized.envelope.metadata["actor_chat_id"] == "-100777"
    assert normalized.envelope.metadata["actor_chat_type"] == "supergroup"
    assert normalized.envelope.metadata["actor_chat_title"] == "Anonymous Admin"
    assert normalized.envelope.metadata["actor_chat_username"] == "synthetic_actor"
