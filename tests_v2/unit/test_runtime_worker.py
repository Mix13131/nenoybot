from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.runtime import RuntimeEventHandler
from app_v2.workers.main import (
    WorkerLoop,
    _apply_connector_preset_from_env,
    _bootstrap_group_from_env,
    _configure_logging,
    _enable_silence_wakeup_group_from_env,
    _migrate_connector_group_from_env,
)


def claimed(*, scope="personal", event_type="private_message", event_id="e1"):
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        scope_type=scope,
        scope_id="u1" if scope == "personal" else "-1001",
        actor_user_id="u1",
        created_at=datetime.now(timezone.utc),
        payload={
            "event_id": event_id,
            "event_type": event_type,
            "scope_type": scope,
            "scope_id": "u1" if scope == "personal" else "-1001",
            "actor_user_id": "u1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "message_id": "1",
            "text": "hello",
            "metadata": {},
        },
    )


class Calls:
    def __init__(self): self.items=[]
    def process(self, event): self.items.append(event)
    def collect(self, event): self.items.append(event)


def runtime():
    personal=Calls(); group=Calls(); feedback=Calls()
    return SimpleNamespace(personal_pipeline=personal, group_pipeline=group, feedback_collector=feedback), personal, group, feedback


def test_runtime_routes_personal_to_feedback_then_personal_pipeline():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed())
    assert len(feedback.items) == 1
    assert len(personal.items) == 1
    assert group.items == []
    assert personal.items[0].scope_type is ScopeType.PERSONAL


def test_runtime_routes_group_to_group_pipeline_only():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed(scope="group", event_type="group_message"))
    assert len(group.items) == 1
    assert personal.items == []
    assert feedback.items == []


def test_runtime_reaction_is_feedback_transport_not_personal_reply():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed(event_type="reaction_added"))
    assert len(feedback.items) == 1
    assert personal.items == []
    assert group.items == []


class Unit:
    def __init__(self, result=False, error=None):
        self.result=result; self.error=error; self.calls=0
    def run_once(self):
        self.calls += 1
        if self.error: raise self.error
        return self.result


class MaintenanceUnit:
    def __init__(self, result=None, error=None):
        self.result=result; self.error=error; self.calls=0
    def run_if_due(self):
        self.calls += 1
        if self.error: raise self.error
        return self.result


def test_worker_loop_runs_event_reminder_and_outbox_fairly():
    event=Unit(True); reminder=Unit(True); outbox=Unit(True); maintenance=MaintenanceUnit(None)
    loop=WorkerLoop(event, reminder, outbox, maintenance)
    assert loop.run_once() is True
    assert event.calls == reminder.calls == outbox.calls == maintenance.calls == 1


def test_maintenance_failure_does_not_block_delivery_units():
    event=Unit(False); reminder=Unit(False); outbox=Unit(True)
    maintenance=MaintenanceUnit(error=RuntimeError("maintenance down"))
    loop=WorkerLoop(event, reminder, outbox, maintenance)
    assert loop.run_once() is True
    assert event.calls == 1
    assert reminder.calls == 1
    assert outbox.calls == 1


def test_transport_loggers_are_warning_or_higher(monkeypatch):
    monkeypatch.setenv("NENOY_V2_LOG_LEVEL", "INFO")
    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).setLevel(logging.INFO)

    _configure_logging()

    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        assert logging.getLogger(name).level >= logging.WARNING


def test_group_bootstrap_is_noop_without_env(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE", raising=False)
    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED", raising=False)

    class ShouldNotConstruct:
        def __init__(self, conn):
            raise AssertionError("repo should not be constructed")

    monkeypatch.setattr(worker_main, "GroupContextRepository", ShouldNotConstruct)
    assert _bootstrap_group_from_env(object()) is None


def test_group_bootstrap_activates_exact_unique_title(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE", "Лучшие здесь")
    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED", raising=False)
    calls=[]

    class FakeRepo:
        def __init__(self, conn):
            pass
        def list_groups(self, limit=100):
            return [
                SimpleNamespace(title="Группа НеНой Тест", telegram_chat_id="-1"),
                SimpleNamespace(title="Лучшие здесь", telegram_chat_id="-2"),
            ]
        def configure_friends_test(self, telegram_chat_id, *, profile, enabled=True):
            calls.append((telegram_chat_id, profile, enabled))
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)
    result = _bootstrap_group_from_env(object())

    assert result == {"title": "Лучшие здесь", "telegram_chat_id": "-2"}
    assert len(calls) == 1
    assert calls[0][0] == "-2"
    assert calls[0][2] is True
    assert calls[0][1]["profile"] == "friends"


def test_group_bootstrap_fails_closed_on_ambiguous_title(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE", "Лучшие здесь")
    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED", raising=False)
    calls=[]

    class FakeRepo:
        def __init__(self, conn):
            pass
        def list_groups(self, limit=100):
            return [
                SimpleNamespace(title="Лучшие здесь", telegram_chat_id="-2"),
                SimpleNamespace(title="Лучшие здесь", telegram_chat_id="-3"),
            ]
        def configure_friends_test(self, telegram_chat_id, *, profile, enabled=True):
            calls.append(telegram_chat_id)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)
    assert _bootstrap_group_from_env(object()) is None
    assert calls == []


def test_group_bootstrap_activates_single_recent_unwhitelisted_group(monkeypatch):
    import app_v2.workers.main as worker_main

    now = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE", raising=False)
    monkeypatch.setenv("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED", "1")
    monkeypatch.setenv("NENOY_V2_BOOTSTRAP_RECENT_MINUTES", "60")
    calls=[]

    class FakeRepo:
        def __init__(self, conn):
            pass
        def list_groups(self, limit=100):
            return [
                SimpleNamespace(
                    title="Группа НеНой Тест",
                    telegram_chat_id="-1",
                    is_whitelisted=True,
                    is_active=True,
                    updated_at=now - timedelta(minutes=5),
                ),
                SimpleNamespace(
                    title="Неизвестное реальное имя",
                    telegram_chat_id="-2",
                    is_whitelisted=False,
                    is_active=True,
                    updated_at=now - timedelta(minutes=8),
                ),
                SimpleNamespace(
                    title="Старая группа",
                    telegram_chat_id="-3",
                    is_whitelisted=False,
                    is_active=True,
                    updated_at=now - timedelta(hours=3),
                ),
            ]
        def configure_friends_test(self, telegram_chat_id, *, profile, enabled=True):
            calls.append((telegram_chat_id, profile, enabled))
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)
    result = _bootstrap_group_from_env(object(), now=now)

    assert result == {"title": "Неизвестное реальное имя", "telegram_chat_id": "-2"}
    assert len(calls) == 1
    assert calls[0][0] == "-2"
    assert calls[0][2] is True


def test_group_bootstrap_recent_mode_fails_closed_when_ambiguous(monkeypatch):
    import app_v2.workers.main as worker_main

    now = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE", raising=False)
    monkeypatch.setenv("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED", "1")
    calls=[]

    class FakeRepo:
        def __init__(self, conn):
            pass
        def list_groups(self, limit=100):
            return [
                SimpleNamespace(
                    title="Новая 1",
                    telegram_chat_id="-2",
                    is_whitelisted=False,
                    is_active=True,
                    updated_at=now - timedelta(minutes=5),
                ),
                SimpleNamespace(
                    title="Новая 2",
                    telegram_chat_id="-3",
                    is_whitelisted=False,
                    is_active=True,
                    updated_at=now - timedelta(minutes=7),
                ),
            ]
        def configure_friends_test(self, telegram_chat_id, *, profile, enabled=True):
            calls.append(telegram_chat_id)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)
    assert _bootstrap_group_from_env(object(), now=now) is None
    assert calls == []



def test_worker_loop_runs_silence_wakeup_without_blocking_other_units():
    event=Unit(False); reminder=Unit(False); outbox=Unit(True); maintenance=MaintenanceUnit(None)
    wakeup=MaintenanceUnit(True)
    loop=WorkerLoop(event, reminder, outbox, maintenance, wakeup)

    assert loop.run_once() is True
    assert wakeup.calls == 1
    assert event.calls == reminder.calls == outbox.calls == maintenance.calls == 1


def test_silence_wakeup_failure_is_fail_silent_for_worker_loop():
    event=Unit(False); reminder=Unit(False); outbox=Unit(True); maintenance=MaintenanceUnit(None)
    wakeup=MaintenanceUnit(error=RuntimeError("wakeup down"))
    loop=WorkerLoop(event, reminder, outbox, maintenance, wakeup)

    assert loop.run_once() is True
    assert wakeup.calls == 1
    assert outbox.calls == 1



def test_silence_wakeup_profile_patch_preserves_existing_group_character(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_ENABLE_SILENCE_WAKEUP_GROUP_TITLE",
        "Лучшие ЗДЕСЬ",
    )
    calls = []

    existing_profile = {
        "profile": "friends",
        "initiative": 7,
        "humor": 9,
        "sarcasm": 9,
        "roast": 8,
        "callback": 9,
        "profanity_level": 5,
        "custom_second_group_flag": "keep-me",
    }

    class FakeRepo:
        def __init__(self, conn):
            pass

        def list_groups(self, limit=100):
            return [
                SimpleNamespace(
                    title="Группа НеНой Тест",
                    telegram_chat_id="-1",
                    is_whitelisted=True,
                    is_active=True,
                    profile={"profile": "friends"},
                ),
                SimpleNamespace(
                    title="Лучшие ЗДЕСЬ",
                    telegram_chat_id="-2",
                    is_whitelisted=True,
                    is_active=True,
                    profile=existing_profile,
                ),
            ]

        def set_group_profile(self, telegram_chat_id, profile):
            calls.append((telegram_chat_id, profile))
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)

    result = _enable_silence_wakeup_group_from_env(object())

    assert result is not None
    assert result["title"] == "Лучшие ЗДЕСЬ"
    assert len(calls) == 1
    assert calls[0][0] == "-2"
    profile = calls[0][1]
    assert profile["initiative"] == 7
    assert profile["sarcasm"] == 9
    assert profile["roast"] == 8
    assert profile["custom_second_group_flag"] == "keep-me"
    assert profile["timezone"] == "Europe/Moscow"
    assert profile["silence_wakeup_enabled"] is True
    assert profile["silence_wakeup_after_minutes"] == 180
    assert profile["silence_wakeup_daily_limit"] == 1


def test_silence_wakeup_profile_patch_requires_existing_approved_group(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_ENABLE_SILENCE_WAKEUP_GROUP_TITLE",
        "Лучшие ЗДЕСЬ",
    )
    calls = []

    class FakeRepo:
        def __init__(self, conn):
            pass

        def list_groups(self, limit=100):
            return [
                SimpleNamespace(
                    title="Лучшие ЗДЕСЬ",
                    telegram_chat_id="-2",
                    is_whitelisted=False,
                    is_active=True,
                    profile={"profile": "friends"},
                ),
            ]

        def set_group_profile(self, telegram_chat_id, profile):
            calls.append((telegram_chat_id, profile))
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)

    assert _enable_silence_wakeup_group_from_env(object()) is None
    assert calls == []


def test_silence_wakeup_profile_patch_fails_closed_on_ambiguous_title(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_ENABLE_SILENCE_WAKEUP_GROUP_TITLE",
        "Лучшие ЗДЕСЬ",
    )
    calls = []

    class FakeRepo:
        def __init__(self, conn):
            pass

        def list_groups(self, limit=100):
            common = dict(
                title="Лучшие ЗДЕСЬ",
                is_whitelisted=True,
                is_active=True,
                profile={"profile": "friends"},
            )
            return [
                SimpleNamespace(telegram_chat_id="-2", **common),
                SimpleNamespace(telegram_chat_id="-3", **common),
            ]

        def set_group_profile(self, telegram_chat_id, profile):
            calls.append((telegram_chat_id, profile))
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeRepo)

    assert _enable_silence_wakeup_group_from_env(object()) is None
    assert calls == []



def test_connector_migration_exact_title_preserves_legacy_profile(monkeypatch):
    import app_v2.workers.main as worker_main
    from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext

    monkeypatch.setenv(
        "NENOY_V2_MIGRATE_CONNECTOR_GROUP_TITLE",
        "Группа НеНой Тест",
    )
    created = []
    legacy_profile = {
        "profile": "friends",
        "unsolicited_enabled": True,
        "initiative": 3,
        "humor": 8,
        "sarcasm": 8,
        "roast": 7,
        "callback": 8,
        "timezone": "Europe/Moscow",
        "silence_wakeup_enabled": True,
    }

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            return [
                SimpleNamespace(
                    title="Группа НеНой Тест",
                    telegram_chat_id="-1",
                    is_whitelisted=True,
                    is_active=True,
                    profile=legacy_profile,
                ),
            ]

        def load(self, telegram_chat_id, actor_user_id):
            return GroupContext(
                internal_chat_id=1,
                telegram_chat_id="-1",
                title="Группа НеНой Тест",
                is_whitelisted=True,
                is_active=True,
                silent_until=None,
                profile=dict(legacy_profile),
                participant=ParticipantContext(telegram_user_id=None),
            )

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def create(self, **kwargs):
            created.append(kwargs)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    result = _migrate_connector_group_from_env(object())

    assert result == {
        "title": "Группа НеНой Тест",
        "created": True,
        "version": 1,
    }
    assert len(created) == 1
    connector = created[0]["config"]
    assert connector.identity.preset == "friends"
    assert connector.behavior.initiative == 3
    assert connector.personality.values["humor"] == 8
    assert connector.personality.values["roast"] == 7
    assert connector.memory.cross_connector_memory is False
    assert created[0]["scope_type"] == "group"


def test_connector_migration_requires_approved_unique_group(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_MIGRATE_CONNECTOR_GROUP_TITLE",
        "Лучшие ЗДЕСЬ",
    )
    created = []

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            return [
                SimpleNamespace(
                    title="Лучшие ЗДЕСЬ",
                    telegram_chat_id="-2",
                    is_whitelisted=False,
                    is_active=True,
                    profile={},
                ),
            ]

        def load(self, *args, **kwargs):
            raise AssertionError("must not load unapproved group")

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def create(self, **kwargs):
            created.append(kwargs)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    assert _migrate_connector_group_from_env(object()) is None
    assert created == []



def test_connector_migration_fails_closed_when_exact_title_is_ambiguous_beyond_admin_list(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_MIGRATE_CONNECTOR_GROUP_TITLE",
        "Повторяющееся имя",
    )
    created = []

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            return [
                SimpleNamespace(
                    title=title,
                    telegram_chat_id="-2",
                    is_whitelisted=True,
                    is_active=True,
                    profile={},
                ),
                SimpleNamespace(
                    title=title,
                    telegram_chat_id="-999",
                    is_whitelisted=True,
                    is_active=True,
                    profile={},
                ),
            ]

        def load(self, *args, **kwargs):
            raise AssertionError("ambiguous title must not load a group")

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def create(self, **kwargs):
            created.append(kwargs)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    assert _migrate_connector_group_from_env(object()) is None
    assert created == []



def test_connector_preset_onboarding_is_noop_without_env(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.delenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        raising=False,
    )
    monkeypatch.delenv("NENOY_V2_CONNECTOR_PRESET_NAME", raising=False)

    class ShouldNotConstruct:
        def __init__(self, conn):
            raise AssertionError("repos must not be constructed")

    monkeypatch.setattr(worker_main, "GroupContextRepository", ShouldNotConstruct)
    assert _apply_connector_preset_from_env(object()) is None


def test_connector_preset_onboarding_requires_both_env_values(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        "НеНой Lab — Бриллиантовый голос",
    )
    monkeypatch.delenv("NENOY_V2_CONNECTOR_PRESET_NAME", raising=False)

    class ShouldNotConstruct:
        def __init__(self, conn):
            raise AssertionError("repos must not be constructed")

    monkeypatch.setattr(worker_main, "GroupContextRepository", ShouldNotConstruct)
    assert _apply_connector_preset_from_env(object()) is None


def test_connector_preset_onboarding_creates_registry_and_whitelists(monkeypatch):
    import app_v2.workers.main as worker_main

    title = "НеНой Lab — Бриллиантовый голос"
    monkeypatch.setenv("NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE", title)
    monkeypatch.setenv(
        "NENOY_V2_CONNECTOR_PRESET_NAME",
        "education_community_v1",
    )
    whitelist_calls = []
    created = []

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, value):
            return [
                SimpleNamespace(
                    title=value,
                    telegram_chat_id="-42",
                    is_whitelisted=False,
                    is_active=True,
                    profile={"legacy_sentinel": "keep"},
                )
            ]

        def set_whitelisted(self, scope_id, enabled):
            whitelist_calls.append((scope_id, enabled))
            return True

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def get_for_scope(self, scope_type, scope_id):
            return None

        def create(self, **kwargs):
            created.append(kwargs)
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    result = _apply_connector_preset_from_env(object())

    assert result == {
        "title": title,
        "preset": "education_community_v1",
        "created": True,
        "version": 1,
        "whitelisted": True,
    }
    assert whitelist_calls == [("-42", True)]
    assert len(created) == 1
    config = created[0]["config"]
    assert config.identity.role == "community_cohost"
    assert config.identity.preset == "education"
    assert config.behavior.silence_wakeup_enabled is False
    assert config.personality.values["warmth"] == 9
    assert config.memory.cross_connector_memory is False


def test_connector_preset_onboarding_unknown_preset_fails_closed(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        "НеНой Lab — Бриллиантовый голос",
    )
    monkeypatch.setenv("NENOY_V2_CONNECTOR_PRESET_NAME", "does_not_exist")
    whitelist_calls = []

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, value):
            return [
                SimpleNamespace(
                    title=value,
                    telegram_chat_id="-42",
                    is_whitelisted=False,
                    is_active=True,
                    profile={},
                )
            ]

        def set_whitelisted(self, scope_id, enabled):
            whitelist_calls.append((scope_id, enabled))
            return True

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def get_for_scope(self, scope_type, scope_id):
            return None

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    assert _apply_connector_preset_from_env(object()) is None
    assert whitelist_calls == []



def test_connector_preset_onboarding_runtime_failure_rolls_back_shared_connection(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        "НеНой Lab — Бриллиантовый голос",
    )
    monkeypatch.setenv(
        "NENOY_V2_CONNECTOR_PRESET_NAME",
        "education_community_v1",
    )

    class FakeConn:
        def __init__(self):
            self.rollback_calls = 0
            self.commit_calls = 0

        def rollback(self):
            self.rollback_calls += 1

        def commit(self):
            self.commit_calls += 1

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            raise RuntimeError("temporary database read failure")

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    conn = FakeConn()
    assert _apply_connector_preset_from_env(conn) is None
    assert conn.rollback_calls == 1
    assert conn.commit_calls == 0


def test_connector_preset_onboarding_success_closes_read_transaction(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        "НеНой Lab — Бриллиантовый голос",
    )
    monkeypatch.setenv(
        "NENOY_V2_CONNECTOR_PRESET_NAME",
        "education_community_v1",
    )

    class FakeConn:
        def __init__(self):
            self.rollback_calls = 0
            self.commit_calls = 0

        def rollback(self):
            self.rollback_calls += 1

        def commit(self):
            self.commit_calls += 1

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            return [
                SimpleNamespace(
                    title=title,
                    telegram_chat_id="-42",
                    is_whitelisted=False,
                    is_active=True,
                    profile={},
                )
            ]

        def set_whitelisted(self, scope_id, enabled):
            return True

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

        def get_for_scope(self, scope_type, scope_id):
            return None

        def create(self, **kwargs):
            return True

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    conn = FakeConn()
    result = _apply_connector_preset_from_env(conn)

    assert result is not None
    assert conn.rollback_calls == 0
    assert conn.commit_calls == 1


def test_connector_preset_onboarding_rollback_failure_is_not_hidden(monkeypatch):
    import app_v2.workers.main as worker_main

    monkeypatch.setenv(
        "NENOY_V2_APPLY_CONNECTOR_PRESET_GROUP_TITLE",
        "НеНой Lab — Бриллиантовый голос",
    )
    monkeypatch.setenv(
        "NENOY_V2_CONNECTOR_PRESET_NAME",
        "education_community_v1",
    )

    class BrokenConn:
        def rollback(self):
            raise RuntimeError("connection cannot be reset")

    class FakeGroupRepo:
        def __init__(self, conn):
            pass

        def find_groups_by_exact_title(self, title):
            raise RuntimeError("query aborted")

    class FakeConnectorRepo:
        def __init__(self, conn):
            pass

    monkeypatch.setattr(worker_main, "GroupContextRepository", FakeGroupRepo)
    monkeypatch.setattr(worker_main, "ConnectorRepository", FakeConnectorRepo)

    import pytest
    with pytest.raises(RuntimeError, match="cannot be reset"):
        _apply_connector_preset_from_env(BrokenConn())
