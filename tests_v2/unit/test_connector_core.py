from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_v2.domain.enums import EventType, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.connector_resolver import LegacyGroupConnectorResolver
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.group_initiative import GroupInitiativeService
from app_v2.services.personality_engine import PersonalityEngine


NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def group_context(profile=None) -> GroupContext:
    base = {
        "profile": "friends",
        "unsolicited_enabled": True,
        "initiative": 7,
        "humor": 9,
        "sarcasm": 8,
        "roast": 7,
        "callback": 9,
        "profanity_level": 5,
        "profanity_frequency": 3,
        "sensitivity": 8,
        "callback_fatigue_minutes": 180,
        "cooldown_minutes": 20,
        "soft_daily_limit": 4,
        "hard_daily_limit": 8,
        "mute_minutes": 90,
        "bot_share_max": 0.15,
        "bot_share_window_minutes": 90,
        "bot_share_min_messages": 12,
        "timezone": "Europe/Moscow",
        "silence_wakeup_enabled": True,
        "silence_wakeup_after_minutes": 180,
        "silence_wakeup_start_hour": 10,
        "silence_wakeup_end_hour": 22,
        "silence_wakeup_daily_limit": 1,
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


def event() -> EventEnvelope:
    return EventEnvelope(
        event_id="connector-e1",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="10",
        text="обычная реплика",
    )


class EmptyRetrieval:
    def retrieve(self, *args, **kwargs):
        return []


class InitiativeRepo:
    def count_feedback_since(self, scope_id, feedback_types, since):
        return 0

    def count_unsolicited_since(self, scope_id, since):
        return 0

    def last_unsolicited_at(self, scope_id):
        return None

    def count_messages_since(self, scope_id, since):
        return 20

    def set_silent_until(self, scope_id, silent_until):
        return True


def test_legacy_profile_resolves_to_structured_connector() -> None:
    connector = LegacyGroupConnectorResolver().resolve(group_context())

    assert connector.connector_type == "telegram_group"
    assert connector.status == "live"
    assert connector.version == 1
    assert connector.identity.preset == "friends"
    assert connector.identity.role == "group_member"

    assert connector.behavior.unsolicited_enabled is True
    assert connector.behavior.initiative == 7
    assert connector.behavior.cooldown_minutes == 20
    assert connector.behavior.soft_daily_limit == 4
    assert connector.behavior.hard_daily_limit == 8
    assert connector.behavior.timezone == "Europe/Moscow"
    assert connector.behavior.silence_wakeup_enabled is True

    assert connector.personality.values["humor"] == 9
    assert connector.personality.values["sarcasm"] == 8
    assert connector.personality.values["roast"] == 7

    assert connector.memory.scope_mode == "connector"
    assert connector.memory.cross_connector_memory is False
    assert connector.memory.threaded_context_policy == "fail_closed"
    assert "silence_wakeup" in connector.capabilities.enabled_names()


def test_malformed_connector_values_fail_closed() -> None:
    connector = LegacyGroupConnectorResolver().resolve(
        group_context(
            {
                "connector_role": "supreme_overlord",
                "unsolicited_enabled": "disabled",
                "silence_wakeup_enabled": "maybe",
                "soft_daily_limit": 20,
                "hard_daily_limit": 2,
                "initiative": "not-a-number",
            }
        )
    )

    assert connector.identity.role == "group_member"
    assert connector.behavior.unsolicited_enabled is False
    assert connector.behavior.silence_wakeup_enabled is False
    assert connector.behavior.initiative == 6
    assert connector.behavior.hard_daily_limit >= connector.behavior.soft_daily_limit
    assert connector.memory.cross_connector_memory is False


def test_public_connector_state_never_exposes_internal_scope_identifier() -> None:
    connector = LegacyGroupConnectorResolver().resolve(group_context())
    state = connector.public_state()
    dumped = repr(state)

    assert "connector_id" not in state
    assert "-100777" not in dumped
    assert state["memory"]["cross_connector_memory"] is False
    assert state["preset"] == "friends"


def test_connector_behavior_and_personality_match_legacy_profile() -> None:
    ctx = group_context()
    connector = LegacyGroupConnectorResolver().resolve(ctx)
    engine = GroupBehaviorEngine(EmptyRetrieval())
    scene = SceneAnalysis()

    legacy = engine.plan(
        event=event(),
        group_context=ctx,
        scene=scene,
        now=NOW,
    )
    connected = engine.plan(
        event=event(),
        group_context=ctx,
        scene=scene,
        now=NOW,
        connector_config=connector,
    )

    assert connected.state.cooldown_active == legacy.state.cooldown_active
    assert connected.state.initiative_level == legacy.state.initiative_level
    assert connected.state.allow_roast == legacy.state.allow_roast
    assert connected.state.allow_callbacks == legacy.state.allow_callbacks
    assert connected.callback_fatigue_minutes == legacy.callback_fatigue_minutes
    assert connected.memory_usage == legacy.memory_usage

    personality = PersonalityEngine()
    legacy_personality = personality.build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_BANTER,
        scene=legacy.scene,
        context_profile=legacy.context_profile,
    )
    connector_personality = personality.build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_BANTER,
        scene=connected.scene,
        context_profile=connected.context_profile,
    )
    assert connector_personality == legacy_personality


def test_connector_initiative_policy_matches_legacy_profile() -> None:
    ctx = group_context()
    resolver = LegacyGroupConnectorResolver()
    connector = resolver.resolve(ctx)
    repo = InitiativeRepo()
    service = GroupInitiativeService(repo)

    legacy = service.evaluate(
        event=event(),
        group_context=ctx,
        now=NOW,
    )
    connected = service.evaluate(
        event=event(),
        group_context=ctx,
        now=NOW,
        connector_config=connector,
    )

    assert connected.effective_cooldown_minutes == legacy.effective_cooldown_minutes
    assert connected.soft_daily_limit == legacy.soft_daily_limit
    assert connected.hard_daily_limit == legacy.hard_daily_limit
    assert connected.initiative_level == legacy.initiative_level
    assert connected.bot_share == legacy.bot_share


def test_initiative_can_resolve_connector_internally_for_non_pipeline_callers() -> None:
    ctx = group_context()
    service = GroupInitiativeService(
        InitiativeRepo(),
        connector_resolver=LegacyGroupConnectorResolver(),
    )

    result = service.evaluate(
        event=event(),
        group_context=ctx,
        now=NOW,
    )

    assert result.effective_cooldown_minutes == 20
    assert result.soft_daily_limit == 4
    assert result.hard_daily_limit == 8
    assert result.initiative_level == 7
