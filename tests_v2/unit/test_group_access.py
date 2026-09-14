from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.group_access import GroupAccessService


def event(scope: ScopeType = ScopeType.GROUP) -> EventEnvelope:
    return EventEnvelope(
        event_id="evt-group-1",
        event_type=EventType.GROUP_MESSAGE if scope is ScopeType.GROUP else EventType.PRIVATE_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope,
        scope_id="-100777" if scope is ScopeType.GROUP else "123",
        actor_user_id="123",
        message_id="10",
        text="Привет",
    )


def group_context(*, whitelisted: bool = True, active: bool = True) -> GroupContext:
    return GroupContext(
        internal_chat_id=7,
        telegram_chat_id="-100777",
        title="Friends",
        is_whitelisted=whitelisted,
        is_active=active,
        silent_until=None,
        profile={"profile": "friends", "roast": 9, "profanity_level": 8},
        participant=ParticipantContext(
            telegram_user_id="123",
            display_name="Anton",
            role="member",
            profile={"roast_tolerance": 7},
        ),
    )


class FakeRepo:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def load(self, telegram_chat_id, actor_telegram_user_id):
        self.calls.append((telegram_chat_id, actor_telegram_user_id))
        return self.context


def test_approved_group_is_allowed_and_profiles_are_preserved() -> None:
    repo = FakeRepo(group_context())
    result = GroupAccessService(repo).evaluate(event())

    assert result.allowed is True
    assert result.reason == "allowed"
    assert result.context is not None
    assert result.context.profile["profile"] == "friends"
    assert result.context.participant.profile["roast_tolerance"] == 7
    assert repo.calls == [("-100777", "123")]


def test_non_whitelisted_group_is_denied() -> None:
    result = GroupAccessService(FakeRepo(group_context(whitelisted=False))).evaluate(event())
    assert result.allowed is False
    assert result.reason == "group_not_whitelisted"


def test_inactive_group_is_denied() -> None:
    result = GroupAccessService(FakeRepo(group_context(active=False))).evaluate(event())
    assert result.allowed is False
    assert result.reason == "group_inactive"


def test_unknown_group_is_denied() -> None:
    result = GroupAccessService(FakeRepo(None)).evaluate(event())
    assert result.allowed is False
    assert result.reason == "unknown_group"
    assert result.context is None


def test_private_event_never_enters_group_access() -> None:
    repo = FakeRepo(group_context())
    result = GroupAccessService(repo).evaluate(event(ScopeType.PERSONAL))
    assert result.allowed is False
    assert result.reason == "not_group_scope"
    assert repo.calls == []


def test_silent_until_does_not_revoke_whitelist_access() -> None:
    context = group_context()
    context = GroupContext(
        **{**context.__dict__, "silent_until": datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)}
    )
    result = GroupAccessService(FakeRepo(context)).evaluate(
        event(),
        now=datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc),
    )
    assert result.allowed is True
    assert result.context is not None
    assert result.context.silent_until is not None
