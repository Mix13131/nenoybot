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


def test_group_reaction_capabilities_honor_allowlist_and_permission() -> None:
    result = client(
        lambda request: httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": -10055,
                    "type": "supergroup",
                    "available_reactions": [
                        {"type": "emoji", "emoji": "👍"},
                        {"type": "emoji", "emoji": "😂"},
                        {"type": "custom_emoji", "custom_emoji_id": "custom-1"},
                    ],
                    "permissions": {
                        "can_send_messages": True,
                        "can_react_to_messages": True,
                    },
                },
            },
        )
    ).get_reaction_capabilities("-10055")

    assert result.status == "available"
    assert result.supports("👍") is True
    assert result.supports("😂") is True
    assert result.supports("🔥") is False


def test_group_reaction_capabilities_omitted_allowlist_means_ordinary_emoji_allowed() -> None:
    result = client(
        lambda request: httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": -10055,
                    "type": "group",
                    "permissions": {"can_send_messages": True},
                },
            },
        )
    ).get_reaction_capabilities("-10055")

    assert result.status == "available"
    assert result.allowed_emojis is None
    assert result.supports("👍") is True


@pytest.mark.parametrize(
    "permissions",
    [
        {"can_react_to_messages": False, "can_send_messages": True},
        {"can_send_messages": False},
    ],
)
def test_group_reaction_capabilities_fail_closed_on_explicit_permission_block(permissions) -> None:
    result = client(
        lambda request: httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": -10055,
                    "type": "supergroup",
                    "permissions": permissions,
                },
            },
        )
    ).get_reaction_capabilities("-10055")

    assert result.supports("👍") is False


def test_group_reaction_capabilities_chat_lookup_unavailable_is_not_supported() -> None:
    result = client(
        lambda request: httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: chat not found"},
        )
    ).get_reaction_capabilities("-10055")

    assert result.status == "unavailable"
    assert result.supports("👍") is False
