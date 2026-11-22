"""Bounded Telegram transport for the separate coach, with sanitized failures."""
from __future__ import annotations

import logging
from typing import Any

import httpx


class CoachTelegramError(RuntimeError):
    """No token-bearing URL, provider response or participant text in the error."""


class CoachTelegramClient:
    def __init__(self, token: str, *, client: Any | None = None, timeout_seconds: float = 10.0):
        if not token:
            raise CoachTelegramError("telegram_token_missing")
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def close(self):
        if self._owns_client:
            self._client.close()

    def _call(self, method: str, body: dict[str, Any]):
        try:
            response = self._client.post(f"https://api.telegram.org/bot{self._token}/{method}", json=body)
            if response.status_code >= 400:
                raise CoachTelegramError("telegram_http_error")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise CoachTelegramError("telegram_api_rejected")
            return payload.get("result")
        except CoachTelegramError:
            raise
        except Exception:
            # Even chaining an httpx exception can reveal its credential-bearing URL.
            raise CoachTelegramError("telegram_result_unknown") from None

    def send(self, output):
        text = output.text
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-16-le")) // 2 > 4096:
            raise CoachTelegramError("invalid_output_text")
        if type(output.chat_id) is not int or output.chat_id >= 0:
            raise CoachTelegramError("invalid_output_scope")
        if type(output.message_thread_id) is not int or output.message_thread_id <= 0:
            raise CoachTelegramError("invalid_output_topic")
        body = {
            "chat_id": output.chat_id, "message_thread_id": output.message_thread_id,
            "text": text, "link_preview_options": {"is_disabled": True},
        }
        if output.request_message_id is not None:
            if type(output.request_message_id) is not int or output.request_message_id <= 0:
                raise CoachTelegramError("invalid_reply_message")
            body["reply_parameters"] = {"message_id": output.request_message_id}
        result = self._call("sendMessage", body)
        if not isinstance(result, dict) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
            raise CoachTelegramError("telegram_result_unknown")
        if not isinstance(result.get("chat"), dict) or result["chat"].get("id") != output.chat_id or result.get("message_thread_id") != output.message_thread_id:
            raise CoachTelegramError("telegram_result_unknown")
        return result

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe", {})
        if not isinstance(result, dict) or result.get("is_bot") is not True or type(result.get("id")) is not int:
            raise CoachTelegramError("telegram_identity_unknown")
        return result

    def check_group_access(self, *, chat_id: int, bot_user_id: int, can_read_all_group_messages: bool):
        chat = self._call("getChat", {"chat_id": chat_id})
        if not isinstance(chat, dict) or chat.get("id") != chat_id or chat.get("type") != "supergroup" or chat.get("is_forum") is not True:
            raise CoachTelegramError("configured_group_is_not_a_forum")
        member = self._call("getChatMember", {"chat_id": chat_id, "user_id": bot_user_id})
        if not isinstance(member, dict) or member.get("status") not in {"member", "administrator", "creator"}:
            raise CoachTelegramError("bot_not_in_configured_group")
        if member["status"] == "member" and not can_read_all_group_messages:
            raise CoachTelegramError("bot_cannot_receive_ordinary_group_messages")

    def configure_webhook(self, *, public_base_url: str, secret: str):
        expected = public_base_url.rstrip("/") + "/webhooks/telegram"
        existing = self._call("getWebhookInfo", {})
        if not isinstance(existing, dict) or not isinstance(existing.get("url"), str):
            raise CoachTelegramError("telegram_existing_webhook_unknown")
        if existing["url"] and existing["url"] != expected:
            raise CoachTelegramError("telegram_webhook_belongs_to_another_deployment")
        result = self._call("setWebhook", {
            "url": expected, "secret_token": secret,
            "allowed_updates": ["message", "edited_message"], "drop_pending_updates": False,
        })
        if result is not True:
            raise CoachTelegramError("telegram_webhook_rejected")
        info = self._call("getWebhookInfo", {})
        if not isinstance(info, dict) or info.get("url") != expected:
            raise CoachTelegramError("telegram_webhook_not_verified")
        return {"verified": True, "pending_update_count": info.get("pending_update_count", 0),
                "last_error_present": bool(info.get("last_error_message"))}

    def configure_commands(self):
        # Natural language is the participant interface. Existing slash aliases
        # remain compatible, but installation does not present a command form.
        result = self._call("setMyCommands", {"commands": []})
        if result is not True:
            raise CoachTelegramError("telegram_commands_rejected")
