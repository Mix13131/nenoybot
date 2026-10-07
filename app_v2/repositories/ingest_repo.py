from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app_v2.adapters.telegram_webhook import NormalizedTelegramUpdate
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.group_defaults import new_group_profile


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

    def upsert_user(
        self,
        user: dict[str, Any] | None,
        *,
        observed_at: datetime | None = None,
        observed_update_id: int | None = None,
        username_observed: bool = True,
    ) -> int | None:
        if not user or user.get("id") is None:
            return None
        parts = [user.get("first_name"), user.get("last_name")]
        display_name = " ".join(part for part in parts if part) or user.get("username")
        observed = observed_at or datetime.now(timezone.utc)
        username = user.get("username") if username_observed else None
        row = self.conn.execute(
            """
            INSERT INTO users(
                telegram_user_id, display_name, username,
                identity_observed_at, identity_observed_update_id, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET display_name = CASE
                    WHEN users.identity_observed_at IS NULL
                      OR EXCLUDED.identity_observed_at > users.identity_observed_at
                      OR (
                          EXCLUDED.identity_observed_at = users.identity_observed_at
                          AND users.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id > users.identity_observed_update_id
                      )
                    THEN COALESCE(EXCLUDED.display_name, users.display_name)
                    ELSE users.display_name
                END,
                username = CASE
                    WHEN %s AND (
                        users.identity_observed_at IS NULL
                        OR EXCLUDED.identity_observed_at > users.identity_observed_at
                        OR (
                            EXCLUDED.identity_observed_at = users.identity_observed_at
                            AND users.identity_observed_update_id IS NOT NULL
                            AND EXCLUDED.identity_observed_update_id IS NOT NULL
                            AND EXCLUDED.identity_observed_update_id > users.identity_observed_update_id
                        )
                    )
                    THEN EXCLUDED.username
                    ELSE users.username
                END,
                identity_observed_at = CASE
                    WHEN users.identity_observed_at IS NULL
                      OR EXCLUDED.identity_observed_at > users.identity_observed_at
                      OR (
                          EXCLUDED.identity_observed_at = users.identity_observed_at
                          AND users.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id > users.identity_observed_update_id
                      )
                    THEN EXCLUDED.identity_observed_at
                    ELSE users.identity_observed_at
                END,
                identity_observed_update_id = CASE
                    WHEN users.identity_observed_at IS NULL
                      OR EXCLUDED.identity_observed_at > users.identity_observed_at
                      OR (
                          EXCLUDED.identity_observed_at = users.identity_observed_at
                          AND users.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id IS NOT NULL
                          AND EXCLUDED.identity_observed_update_id > users.identity_observed_update_id
                      )
                    THEN EXCLUDED.identity_observed_update_id
                    ELSE users.identity_observed_update_id
                END,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                int(user["id"]),
                display_name,
                username,
                observed,
                observed_update_id,
                username_observed,
            ),
        ).fetchone()
        return int(row[0])

    def upsert_chat(self, chat: dict[str, Any]) -> int:
        chat_type = "private" if chat.get("type") == "private" else "group"
        is_telegram_group = chat.get("type") in {"group", "supergroup"}
        title = chat.get("title")
        if not title and chat_type == "private":
            title = chat.get("username") or chat.get("first_name")
        # Approval/defaults apply ONLY to INSERT. An existing denial from any
        # administrative path is never undone by ordinary traffic.
        row = self.conn.execute(
            """
            INSERT INTO chats(
                telegram_chat_id, chat_type, title, is_whitelisted,
                group_profile, updated_at
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
            ON CONFLICT (telegram_chat_id) DO UPDATE
            SET chat_type = EXCLUDED.chat_type,
                title = EXCLUDED.title,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                int(chat["id"]), chat_type, title, is_telegram_group,
                json.dumps(new_group_profile() if is_telegram_group else {}, ensure_ascii=False),
            ),
        ).fetchone()
        return int(row[0])

    def upsert_member(
        self,
        chat_id: int,
        user_id: int | None,
        *,
        user: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        observed_update_id: int | None = None,
        username_observed: bool = True,
        alias_limit: int = 12,
    ) -> None:
        if user_id is None or not user:
            return
        observed = observed_at or datetime.now(timezone.utc)
        display_name = " ".join(
            str(part).strip()
            for part in (user.get("first_name"), user.get("last_name"))
            if part
        ) or None
        username = user.get("username") if username_observed else None
        self.conn.execute(
            """
            INSERT INTO chat_members(
                chat_id, user_id, current_username, current_display_name,
                first_seen_at, last_seen_at, identity_observed_at,
                identity_observed_update_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET first_seen_at = LEAST(chat_members.first_seen_at, EXCLUDED.first_seen_at),
                last_seen_at = GREATEST(chat_members.last_seen_at, EXCLUDED.last_seen_at)
            """,
            (
                chat_id,
                user_id,
                username,
                display_name,
                observed,
                observed,
                observed,
                observed_update_id,
            ),
        )
        row = self.conn.execute(
            """SELECT current_username, current_display_name, aliases,
                      identity_observed_at, identity_observed_update_id
               FROM chat_members
               WHERE chat_id = %s AND user_id = %s
               FOR UPDATE""",
            (chat_id, user_id),
        ).fetchone()
        if row is None:
            return

        old_username, old_display, raw_aliases, current_observed_at, current_update_id = row
        if current_observed_at is not None:
            if observed < current_observed_at:
                return
            if observed == current_observed_at:
                if current_update_id is None:
                    return
                if observed_update_id is None or observed_update_id <= int(current_update_id):
                    return

        aliases = [dict(item) for item in (raw_aliases or []) if isinstance(item, dict)]
        changes = (
            ("username", old_username, username if username_observed else old_username),
            ("display_name", old_display, display_name or old_display),
        )
        for kind, old, new in changes:
            if old and old != new:
                aliases = [
                    item
                    for item in aliases
                    if not (
                        item.get("kind") == kind
                        and str(item.get("value", "")).casefold() == str(old).casefold()
                    )
                ]
                aliases.append(
                    {
                        "kind": kind,
                        "value": str(old),
                        "last_used_at": observed.isoformat(),
                    }
                )
        aliases = aliases[-max(1, min(alias_limit, 50)):]
        self.conn.execute(
            """UPDATE chat_members
               SET current_username = CASE
                       WHEN %s THEN %s
                       ELSE current_username
                   END,
                   current_display_name = COALESCE(%s, current_display_name),
                   aliases = %s::jsonb,
                   identity_observed_at = %s,
                   identity_observed_update_id = %s
               WHERE chat_id = %s AND user_id = %s""",
            (
                username_observed,
                username,
                display_name,
                json.dumps(aliases, ensure_ascii=False),
                observed,
                observed_update_id,
                chat_id,
                user_id,
            ),
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
