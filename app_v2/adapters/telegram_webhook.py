from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope


_NAME_ADDRESS_RE = re.compile(
    r"^\s*(?:(?:эй|слушай)[\s,:;.!?—-]+)?неной(?:\s+бро)?(?=$|[\s,:;.!?—-])",
    flags=re.IGNORECASE,
)
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", flags=re.UNICODE)
_INCOMPLETE_ENDINGS = {
    "а", "и", "но", "если", "чтобы", "что", "как", "когда", "потому",
    "про", "на", "в", "с", "по", "для", "ты", "можешь", "сможешь",
    "будешь", "хочешь", "можно", "надо", "нужно", "слушай", "короче",
    "кстати",
}


@dataclass(frozen=True)
class NormalizedTelegramUpdate:
    telegram_update_id: int
    envelope: EventEnvelope
    telegram_user: dict[str, Any] | None
    telegram_chat: dict[str, Any]
    message: dict[str, Any] | None

    @property
    def is_group(self) -> bool:
        return self.envelope.scope_type is ScopeType.GROUP


def _utc_from_epoch(value: int | float | None) -> datetime:
    timestamp = 0 if value is None else value
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _scope_for_chat(chat: dict[str, Any]) -> ScopeType:
    return ScopeType.PERSONAL if chat.get("type") == "private" else ScopeType.GROUP


def _mention_metadata(message: dict[str, Any]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for entity in message.get("entities") or []:
        if entity.get("type") not in {"mention", "text_mention"}:
            continue
        item = {
            "type": entity.get("type"),
            "offset": entity.get("offset"),
            "length": entity.get("length"),
        }
        if entity.get("user") and entity["user"].get("id") is not None:
            item["user_id"] = str(entity["user"]["id"])
        mentions.append(item)
    return mentions


def _is_name_address(text: str) -> bool:
    """Treat a clear vocative use of the product name as a direct address.

    We intentionally do not use `бро` alone as an alias because it is common
    group-chat vocabulary and would cause false direct mentions.
    """

    return bool(_NAME_ADDRESS_RE.search(text))


def _is_direct_mention(
    message: dict[str, Any],
    *,
    bot_username: str | None,
    bot_user_id: str | None,
) -> bool:
    text = message.get("text") if isinstance(message.get("text"), str) else ""
    username = (bot_username or "").strip().lstrip("@").lower()
    if username and f"@{username}" in text.lower():
        return True

    if bot_user_id:
        target = str(bot_user_id)
        for entity in message.get("entities") or []:
            if entity.get("type") != "text_mention":
                continue
            user = entity.get("user") if isinstance(entity.get("user"), dict) else None
            if user and user.get("id") is not None and str(user["id"]) == target:
                return True

    return _is_name_address(text)


def _address_body(
    text: str,
    *,
    bot_username: str | None,
    name_address: bool,
    reply_to_bot: bool,
) -> str:
    """Return the conversational body after a leading bot address when possible."""

    value = text.strip()
    if reply_to_bot:
        return value

    username = (bot_username or "").strip().lstrip("@")
    if username:
        username_re = re.compile(
            rf"^\s*@{re.escape(username)}(?=$|[\s,:;.!?—-])",
            flags=re.IGNORECASE,
        )
        match = username_re.search(value)
        if match:
            return value[match.end():].lstrip(" \t,:;.!?—-")

    if name_address:
        match = _NAME_ADDRESS_RE.search(value)
        if match:
            return value[match.end():].lstrip(" \t,:;.!?—-")

    return value


def _looks_like_incomplete_turn(text: str) -> bool:
    """Conservative heuristic for a message likely to be continued immediately.

    It is intentionally narrow: the purpose is a tiny debounce for obvious
    half-sentences, not semantic sentence completion.
    """

    value = " ".join(text.strip().split())
    if not value or len(value) > 60:
        return False
    if value.endswith((".", "!", "?", "…")):
        return False
    if value.endswith((",", ":", ";", "—", "-")):
        return True

    words = [item.lower() for item in _WORD_RE.findall(value)]
    if not words or len(words) > 7:
        return False
    return words[-1] in _INCOMPLETE_ENDINGS


def _normalize_message(
    update_id: int,
    message: dict[str, Any],
    *,
    edited: bool,
    bot_username: str | None,
    bot_user_id: str | None,
) -> NormalizedTelegramUpdate | None:
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None

    actor = message.get("from") if isinstance(message.get("from"), dict) else None
    scope_type = _scope_for_chat(chat)
    text = message.get("text") if isinstance(message.get("text"), str) else None
    reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else None
    reply_from = reply.get("from") if reply and isinstance(reply.get("from"), dict) else None
    reply_to_bot = bool(reply_from and reply_from.get("is_bot"))
    name_address = _is_name_address(text or "")
    direct_mention = _is_direct_mention(
        message,
        bot_username=bot_username,
        bot_user_id=bot_user_id,
    )

    if edited:
        event_type = EventType.EDITED_MESSAGE
    elif reply_to_bot:
        event_type = EventType.REPLY_TO_BOT
    elif text and text.lstrip().startswith("/"):
        event_type = EventType.COMMAND
    elif scope_type is ScopeType.PERSONAL:
        event_type = EventType.PRIVATE_MESSAGE
    elif direct_mention:
        event_type = EventType.DIRECT_MENTION
    else:
        event_type = EventType.GROUP_MESSAGE

    incomplete_turn = False
    if (
        scope_type is ScopeType.GROUP
        and text
        and event_type in {EventType.DIRECT_MENTION, EventType.REPLY_TO_BOT}
    ):
        body = _address_body(
            text,
            bot_username=bot_username,
            name_address=name_address,
            reply_to_bot=reply_to_bot,
        )
        incomplete_turn = _looks_like_incomplete_turn(body)

    message_id = message.get("message_id")
    envelope = EventEnvelope(
        event_id=f"tg:{update_id}",
        event_type=event_type,
        occurred_at=_utc_from_epoch(message.get("date") or message.get("edit_date")),
        scope_type=scope_type,
        scope_id=str(chat["id"]),
        actor_user_id=str(actor["id"]) if actor and actor.get("id") is not None else None,
        message_id=str(message_id) if message_id is not None else None,
        reply_to_message_id=(
            str(reply["message_id"]) if reply and reply.get("message_id") is not None else None
        ),
        text=text,
        metadata={
            "telegram_chat_type": chat.get("type"),
            "reply_to_bot": reply_to_bot,
            "direct_mention": direct_mention,
            "name_address": name_address,
            "incomplete_turn": incomplete_turn,
            "mentions": _mention_metadata(message),
            "edited": edited,
        },
    )
    return NormalizedTelegramUpdate(
        telegram_update_id=update_id,
        envelope=envelope,
        telegram_user=actor,
        telegram_chat=chat,
        message=message,
    )


def _normalize_reaction(update_id: int, reaction: dict[str, Any]) -> NormalizedTelegramUpdate | None:
    chat = reaction.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    actor = reaction.get("user") if isinstance(reaction.get("user"), dict) else None
    new_reaction = reaction.get("new_reaction") or []
    event_type = EventType.REACTION_ADDED if new_reaction else EventType.REACTION_REMOVED
    message_id = reaction.get("message_id")
    envelope = EventEnvelope(
        event_id=f"tg:{update_id}",
        event_type=event_type,
        occurred_at=_utc_from_epoch(reaction.get("date")),
        scope_type=_scope_for_chat(chat),
        scope_id=str(chat["id"]),
        actor_user_id=str(actor["id"]) if actor and actor.get("id") is not None else None,
        message_id=str(message_id) if message_id is not None else None,
        metadata={
            "telegram_chat_type": chat.get("type"),
            "old_reaction": reaction.get("old_reaction") or [],
            "new_reaction": new_reaction,
        },
    )
    return NormalizedTelegramUpdate(
        telegram_update_id=update_id,
        envelope=envelope,
        telegram_user=actor,
        telegram_chat=chat,
        message=None,
    )


def normalize_update(
    update: dict[str, Any],
    *,
    bot_username: str | None = None,
    bot_user_id: str | None = None,
) -> NormalizedTelegramUpdate | None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None

    if isinstance(update.get("edited_message"), dict):
        return _normalize_message(
            update_id,
            update["edited_message"],
            edited=True,
            bot_username=bot_username,
            bot_user_id=bot_user_id,
        )
    if isinstance(update.get("message"), dict):
        return _normalize_message(
            update_id,
            update["message"],
            edited=False,
            bot_username=bot_username,
            bot_user_id=bot_user_id,
        )
    if isinstance(update.get("message_reaction"), dict):
        return _normalize_reaction(update_id, update["message_reaction"])
    return None
