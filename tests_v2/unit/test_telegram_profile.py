from __future__ import annotations

import httpx
import pytest

from app_v2.adapters.telegram_profile import (
    TelegramProfileClient,
    TelegramProfileLookupError,
)


def client(handler):
    return TelegramProfileClient(
        token="123:test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://api.telegram.test",
    )


def test_get_birthdate_reads_privacy_visible_bot_api_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/bot123:test-token/getChat")
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 42,
                    "type": "private",
                    "birthdate": {"day": 14, "month": 5, "year": 1981},
                },
            },
        )

    result = client(handler).get_birthdate("42")

    assert result.status == "available"
    assert result.birthdate is not None
    assert (result.birthdate.day, result.birthdate.month, result.birthdate.year) == (14, 5, 1981)


def test_get_birthdate_missing_field_is_normal_not_shared_state() -> None:
    result = client(
        lambda request: httpx.Response(
            200,
            json={"ok": True, "result": {"id": 42, "type": "private"}},
        )
    ).get_birthdate(42)

    assert result.status == "not_shared"
    assert result.birthdate is None


def test_get_birthdate_chat_not_found_is_normal_unavailable_state() -> None:
    result = client(
        lambda request: httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: chat not found"},
        )
    ).get_birthdate(42)

    assert result.status == "unavailable"
    assert result.birthdate is None


def test_get_birthdate_server_failure_raises_retryable_lookup_error() -> None:
    with pytest.raises(TelegramProfileLookupError, match="telegram_http_500"):
        client(
            lambda request: httpx.Response(
                500,
                json={"ok": False, "description": "down"},
            )
        ).get_birthdate(42)


def test_invalid_birthdate_payload_is_rejected() -> None:
    with pytest.raises(TelegramProfileLookupError, match="invalid_birthdate_payload"):
        client(
            lambda request: httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 42,
                        "type": "private",
                        "birthdate": {"day": 31, "month": 2},
                    },
                },
            )
        ).get_birthdate(42)
