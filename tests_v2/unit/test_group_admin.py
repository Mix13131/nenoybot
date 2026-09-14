from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app_v2.group_admin import (
    FRIENDS_DAY1_PROFILE,
    GroupAdminError,
    activate_friends_group,
    deactivate_group,
    list_groups,
)
from app_v2.repositories.group_context_repo import GroupAdminRecord


class FakeRepo:
    def __init__(self) -> None:
        self.groups = [
            GroupAdminRecord(
                telegram_chat_id="-100777",
                title="Friends",
                is_whitelisted=False,
                is_active=True,
                profile={},
                updated_at=datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc),
            )
        ]
        self.configure_calls = []
        self.whitelist_calls = []
        self.configure_result = True
        self.whitelist_result = True

    def list_groups(self, *, limit=20):
        return self.groups[:limit]

    def configure_friends_test(self, telegram_chat_id, *, profile, enabled=True):
        self.configure_calls.append((telegram_chat_id, profile, enabled))
        return self.configure_result

    def set_whitelisted(self, telegram_chat_id, enabled):
        self.whitelist_calls.append((telegram_chat_id, enabled))
        return self.whitelist_result


def test_day1_profile_is_deliberately_low_initiative() -> None:
    assert FRIENDS_DAY1_PROFILE["profile"] == "friends"
    assert FRIENDS_DAY1_PROFILE["unsolicited_enabled"] is True
    assert FRIENDS_DAY1_PROFILE["initiative"] == 3
    assert FRIENDS_DAY1_PROFILE["roast"] <= 7
    assert FRIENDS_DAY1_PROFILE["profanity_frequency"] <= 3
    assert FRIENDS_DAY1_PROFILE["sensitivity"] >= 8


def test_activate_whitelists_exact_group_with_day1_profile() -> None:
    repo = FakeRepo()
    result = activate_friends_group(repo, "-100777")

    assert result["whitelisted"] is True
    assert result["telegram_chat_id"] == "-100777"
    assert repo.configure_calls == [("-100777", FRIENDS_DAY1_PROFILE, True)]


def test_activate_unknown_group_fails_closed() -> None:
    repo = FakeRepo()
    repo.configure_result = False

    with pytest.raises(GroupAdminError, match="Group not found"):
        activate_friends_group(repo, "-100999")


def test_deactivate_removes_exact_group_from_whitelist() -> None:
    repo = FakeRepo()
    result = deactivate_group(repo, "-100777")

    assert result == {"telegram_chat_id": "-100777", "whitelisted": False}
    assert repo.whitelist_calls == [("-100777", False)]


def test_list_groups_serializes_timestamp_without_secrets() -> None:
    repo = FakeRepo()
    rows = list_groups(repo, limit=10)

    assert rows == [
        {
            "telegram_chat_id": "-100777",
            "title": "Friends",
            "is_whitelisted": False,
            "is_active": True,
            "profile": {},
            "updated_at": "2026-09-14T10:00:00+00:00",
        }
    ]
