from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app_v2.adapters.telegram_webhook import NormalizedTelegramUpdate
from app_v2.domain.enums import EventType, ScopeType


_GROUP_INCOMPLETE_DEBOUNCE_SECONDS = 3.0


class TelegramIngestRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def event_exists(self, telegram_update_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE telegram_update_id = %s",
            (telegram_update_id,),
        ).fetchone()
        return row is not None

    def upsert_user(self, user: dict[str, Any] | None) -> int | None:
        if not user or user.get("id") is None:
            return None
        parts = [user.get("first_name"), user.get("last_name")]
        display_name = " ".join(part for part in parts if part) or user.get("username")
        row = self.conn.execute(
            """
            INSERT INTO users(telegram_user_id, display_name, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (int(user["id"]), display_name),
        ).fetchone()
        return int(row[0])

    def upsert_chat(self, chat: dict[str, Any]) -> int:
        chat_type = "private" if chat.get("type") == "private" else "group"
        title = chat.get("title")
        if not title and chat_type == "private":
            title = chat.get("username") or chat.get("first_name")
        row = self.conn.execute(
            """
            INSERT INTO chats(telegram_chat_id, chat_type, title, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_chat_id) DO UPDATE
            SET chat_type = EXCLUDED.chat_type,
                title = EXCLUDED.title,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (int(chat["id"]), chat_type, title),
        ).fetchone()
        return int(row[0])

    def upsert_member(self, chat_id: int, user_id: int | None) -> None:
        if user_id is None:
            return
        self.conn.execute(
            """
            INSERT INTO chat_members(chat_id, user_id, last_seen_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET last_seen_at = CURRENT_TIMESTAMP
            """,
            (chat_id, user_id),
        )

    def store_message(
        self,
        normalized: NormalizedTelegramUpdate,
        chat_id: int,
        user_id: int | None,
    ) -> None:
        message = normalized.message
        if not message or message.get("message_id") is None:
            return
        created_at = datetime.fromtimestamp(
            message.get("date") or message.get("edit_date") or 0,
            tz=timezone.utc,
        )
        reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else None
        self.conn.execute(
            """
            INSERT INTO messages(
                chat_id, user_id, telegram_message_id, reply_to_message_id,
                text, message_type, created_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, telegram_message_id) DO UPDATE
            SET text = EXCLUDED.text,
                reply_to_message_id = EXCLUDED.reply_to_message_id
            """,
            (
                chat_id,
                user_id,
                int(message["message_id"]),
                int(reply["message_id"]) if reply and reply.get("message_id") is not None else None,
                message.get("text"),
                "text" if isinstance(message.get("text"), str) else "other",
                created_at,
                None,
            ),
        )

    def insert_event(self, normalized: NormalizedTelegramUpdate) -> bool:
        envelope = normalized.envelope
        payload = {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type.value,
            "occurred_at": envelope.occurred_at.isoformat(),
            "scope_type": envelope.scope_type.value,
            "scope_id": envelope.scope_id,
            "actor_user_id": envelope.actor_user_id,
            "message_id": envelope.message_id,
            "reply_to_message_id": envelope.reply_to_message_id,
            "text": envelope.text,
            "metadata": envelope.metadata,
        }
        should_debounce = (
            envelope.scope_type is ScopeType.GROUP
            and envelope.event_type in {EventType.DIRECT_MENTION, EventType.REPLY_TO_BOT}
            and bool(envelope.metadata.get("incomplete_turn"))
        )
        delay_seconds = _GROUP_INCOMPLETE_DEBOUNCE_SECONDS if should_debounce else 0.0
        row = self.conn.execute(
            """
            INSERT INTO events(
                event_id, telegram_update_id, event_type, scope_type, scope_id,
                actor_user_id, payload, status, available_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s::jsonb, 'pending',
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
            )
            ON CONFLICT (telegram_update_id) DO NOTHING
            RETURNING id
            """,
            (
                envelope.event_id,
                normalized.telegram_update_id,
                envelope.event_type.value,
                envelope.scope_type.value,
                envelope.scope_id,
                envelope.actor_user_id,
                json.dumps(payload, ensure_ascii=False),
                delay_seconds,
            ),
        ).fetchone()
        return row is not None
