from __future__ import annotations

import logging

import pytest

from app_v2.telegram_webhook_setup import (
    TelegramWebhookSetupError,
    configure_webhook,
    resolve_webhook_url,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def post(self, url: str, json: dict):
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)

    def get(self, url: str):
        self.calls.append(("GET", url, None))
        return self.responses.pop(0)


def test_resolve_webhook_url_from_railway_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NENOY_V2_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "nenoy-v2-web-production.up.railway.app")
    assert resolve_webhook_url() == "https://nenoy-v2-web-production.up.railway.app/webhooks/telegram"


def test_explicit_public_base_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NENOY_V2_PUBLIC_BASE_URL", "https://bot.example.com/")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "ignored.example")
    assert resolve_webhook_url() == "https://bot.example.com/webhooks/telegram"


def test_configure_webhook_uses_secret_without_printing_or_returning_it() -> None:
    client = FakeClient(
        [
            FakeResponse(200, {"ok": True, "result": True}),
            FakeResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "url": "https://bot.example.com/webhooks/telegram",
                        "pending_update_count": 2,
                    },
                },
            ),
        ]
    )
    info = configure_webhook(
        token="123:secret-token",
        secret="webhook-secret",
        webhook_url="https://bot.example.com/webhooks/telegram",
        client=client,
    )
    assert info.url == "https://bot.example.com/webhooks/telegram"
    assert info.pending_update_count == 2
    post_payload = client.calls[0][2]
    assert post_payload["secret_token"] == "webhook-secret"
    assert post_payload["allowed_updates"] == ["message", "edited_message", "message_reaction"]


def test_configure_webhook_forces_transport_loggers_to_warning() -> None:
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.INFO)

    client = FakeClient(
        [
            FakeResponse(200, {"ok": True, "result": True}),
            FakeResponse(
                200,
                {"ok": True, "result": {"url": "https://bot.example.com/webhooks/telegram"}},
            ),
        ]
    )
    configure_webhook(
        token="123:secret-token",
        secret="webhook-secret",
        webhook_url="https://bot.example.com/webhooks/telegram",
        client=client,
    )

    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        assert logging.getLogger(name).level >= logging.WARNING


def test_rejected_set_webhook_raises_sanitized_error() -> None:
    client = FakeClient([FakeResponse(401, {"ok": False, "description": "bad token"})])
    with pytest.raises(TelegramWebhookSetupError, match="setWebhook request was rejected") as exc:
        configure_webhook(
            token="123:super-secret-token",
            secret="another-secret",
            webhook_url="https://bot.example.com/webhooks/telegram",
            client=client,
        )
    assert "super-secret-token" not in str(exc.value)
    assert "another-secret" not in str(exc.value)


def test_verification_rejects_unexpected_url() -> None:
    client = FakeClient(
        [
            FakeResponse(200, {"ok": True, "result": True}),
            FakeResponse(200, {"ok": True, "result": {"url": "https://wrong.example/webhook"}}),
        ]
    )
    with pytest.raises(TelegramWebhookSetupError, match="URL verification failed"):
        configure_webhook(
            token="123:token",
            secret="secret",
            webhook_url="https://bot.example.com/webhooks/telegram",
            client=client,
        )
