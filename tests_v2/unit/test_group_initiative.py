from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_v2.domain.enums import EventType, PrimaryAction, ReasonCode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.dispatcher import decide
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.group_initiative import GroupInitiativeService


NOW = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


def event(text="обычная болтовня", event_type=EventType.GROUP_MESSAGE):
    return EventEnvelope(
        event_id="e20",
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="20",
        text=text,
    )


def context(profile=None):
    base = {
        "profile": "friends",
        "unsolicited_enabled": True,
        "initiative": 6,
        "roast": 9,
        "callback": 10,
    }
    base.update(profile or {})
    return GroupContext(
        internal_chat_id=7,
        telegram_chat_id="-100777",
        title="Friends",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile=base,
        participant=ParticipantContext(
            telegram_user_id="123",
            display_name="Anton",
            profile={"roast_tolerance": 7},
        ),
    )


class FakeRepo:
    def __init__(self, *, ignored=0, negative=0, positive=0, daily=0, recent=0, messages=20, last=None):
        self.ignored=ignored
        self.negative=negative
        self.positive=positive
        self.daily=daily
        self.recent=recent
        self.messages=messages
        self.last=last
        self.silence_updates=[]

    def count_feedback_since(self, scope_id, feedback_types, since):
        types=set(feedback_types)
        if "ignored" in types:
            return self.ignored
        if "negative" in types:
            return self.negative
        if "positive" in types:
            return self.positive
        return 0

    def count_unsolicited_since(self, scope_id, since):
        return self.recent if since > NOW - timedelta(hours=2) else self.daily

    def last_unsolicited_at(self, scope_id):
        return self.last

    def count_messages_since(self, scope_id, since):
        return self.messages

    def set_silent_until(self, scope_id, silent_until):
        self.silence_updates.append((scope_id, silent_until))
        return True


class EmptyRetrieval:
    def __init__(self): self.calls=[]
    def retrieve(self, *args, **kwargs):
        self.calls.append((args,kwargs))
        return []


def test_two_ignored_interventions_expand_default_cooldown_to_18_minutes() -> None:
    repo=FakeRepo(ignored=2, last=NOW-timedelta(minutes=10))
    snapshot=GroupInitiativeService(repo).evaluate(event=event(), group_context=context(), now=NOW)
    assert snapshot.effective_cooldown_minutes == 18
    assert snapshot.cooldown_active is True
    assert snapshot.ignored_unsolicited_recent == 2


def test_positive_feedback_can_raise_initiative_by_only_one() -> None:
    repo=FakeRepo(positive=100)
    snapshot=GroupInitiativeService(repo).evaluate(event=event(), group_context=context(), now=NOW)
    assert snapshot.initiative_level == 7
    assert snapshot.metadata["positive_bonus"] == 1


def test_negative_feedback_reduces_initiative_faster_than_positive() -> None:
    repo=FakeRepo(negative=1, positive=100)
    snapshot=GroupInitiativeService(repo).evaluate(event=event(), group_context=context(), now=NOW)
    assert snapshot.initiative_level == 5  # base 6 +1 positive -2 negative
    assert snapshot.metadata["negative_penalty"] == 2


def test_bot_share_guardrail_blocks_unsolicited() -> None:
    repo=FakeRepo(recent=2, messages=10)
    snapshot=GroupInitiativeService(repo).evaluate(event=event(), group_context=context(), now=NOW)
    assert snapshot.bot_share == .2
    assert snapshot.bot_share_blocked is True
    assert snapshot.cooldown_active is True


def test_group_profile_overrides_cooldown_and_daily_limits() -> None:
    repo=FakeRepo()
    snapshot=GroupInitiativeService(repo).evaluate(
        event=event(),
        group_context=context({"cooldown_minutes":30,"soft_daily_limit":3,"hard_daily_limit":5}),
        now=NOW,
    )
    assert snapshot.effective_cooldown_minutes == 30
    assert snapshot.soft_daily_limit == 3
    assert snapshot.hard_daily_limit == 5


def test_shut_up_control_sets_silent_until_and_dispatcher_does_not_reply() -> None:
    repo=FakeRepo()
    service=GroupInitiativeService(repo)
    retrieval=EmptyRetrieval()
    behavior=GroupBehaviorEngine(retrieval, initiative_service=service)
    evt=event("@nenoy заткнись", EventType.DIRECT_MENTION)
    plan=behavior.plan(event=evt, group_context=context(), scene=SceneAnalysis(direct_mention=True), now=NOW)
    decision=decide(evt, plan.scene, plan.state)

    assert repo.silence_updates
    assert repo.silence_updates[0][0] == "-100777"
    assert repo.silence_updates[0][1] == NOW + timedelta(minutes=120)
    assert plan.scene.command_intent == "mute"
    assert decision.primary_action is PrimaryAction.IGNORE
    assert decision.reason_codes == [ReasonCode.SILENCE_REQUESTED]
    assert retrieval.calls == []


def test_direct_mention_bypasses_dynamic_hard_limit_and_share_block() -> None:
    repo=FakeRepo(daily=99, recent=9, messages=10, last=NOW-timedelta(minutes=1))
    service=GroupInitiativeService(repo)
    behavior=GroupBehaviorEngine(EmptyRetrieval(), initiative_service=service)
    evt=event("@nenoy ответь", EventType.DIRECT_MENTION)
    plan=behavior.plan(event=evt, group_context=context(), scene=SceneAnalysis(direct_mention=True), now=NOW)
    decision=decide(evt, plan.scene, plan.state)

    assert plan.state.unsolicited_today == 99
    assert plan.state.cooldown_active is True
    assert decision.primary_action is PrimaryAction.REPLY
    assert decision.metadata["unsolicited"] is False
