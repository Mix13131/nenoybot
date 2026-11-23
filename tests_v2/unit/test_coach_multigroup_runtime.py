"""Synthetic deployment and worker coordination; no network or real credentials."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app_v2.coach_config import CoachConfig


BOT = 990001
CHAT = -990101


def joined_config(**changes):
    return replace(CoachConfig(
        environment="test", enabled=True, group_mode="joined_groups",
        database_url="postgresql://synthetic.invalid/synthetic",
        telegram_bot_token=f"{BOT}:synthetic_token_never_real_123456",
        bot_user_id=BOT, bot_username="synthetic_coach_bot", webhook_secret="s" * 32,
        public_base_url="https://synthetic.invalid",
    ), **changes)


class FakeConnection:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def transaction(self):
        return nullcontext(self)

    def execute(self, query, args=None):
        self.queries.append((query, args))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def test_joined_prepare_binds_database_without_precreating_any_chat_or_topic(monkeypatch):
    import app_v2.coach_admin as module
    conn, bound = FakeConnection(), []
    monkeypatch.setattr(module, "connect", lambda *a, **kw: conn)
    monkeypatch.setattr(module, "assert_dedicated_database", lambda *a: None)
    monkeypatch.setattr(module, "run_migrations", lambda url: ["synthetic_migration"])
    monkeypatch.setattr(module, "CoachScopeRepository", lambda connection, *, bot_user_id:
                        SimpleNamespace(bind_identity=lambda: bound.append((connection, bot_user_id))))
    def unexpected_scope(*a, **kw):
        raise AssertionError("Joined mode must wait for received Telegram scopes")
    monkeypatch.setattr(module, "SourceArchiveRepository", lambda connection:
                        SimpleNamespace(configure_scope=unexpected_scope))
    # Even old optional setup values cannot become an allowlist or fabricated scope.
    result = module.prepare(joined_config(chat_id=CHAT, topic_ids=(17, 19)))
    assert result["status"] == "prepared" and result["configured_topics"] == 0
    assert result["participant_grants_created"] == 0
    assert bound == [(conn, BOT)] and conn.queries == []


class TelegramSetup:
    def __init__(self, *, after_read=True, after_id=BOT):
        self.after_read, self.after_id, self.calls = after_read, after_id, []

    def get_me(self):
        self.calls.append("identity")
        return {"id": BOT, "username": "synthetic_coach_bot", "can_read_all_group_messages": False}

    def configure_group_reading(self):
        self.calls.append("reading")
        return {"id": self.after_id, "username": "synthetic_coach_bot",
                "can_read_all_group_messages": self.after_read}

    def check_group_access(self, **kwargs):
        self.calls.append(("group", kwargs))

    def configure_webhook(self, **kwargs):
        self.calls.append("webhook")
        return {"verified": True}

    def configure_commands(self):
        self.calls.append("commands")


def setup_admin(monkeypatch):
    import app_v2.coach_admin as module
    monkeypatch.setattr(module, "connect", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(module, "CoachScopeRepository", lambda *a, **kw:
                        SimpleNamespace(assert_identity=lambda: None))
    return module


def test_joined_setup_changes_reading_policy_then_verifies_webhook_without_fixed_chat(monkeypatch):
    module = setup_admin(monkeypatch)
    client = TelegramSetup()
    result = module.configure_telegram(joined_config(), client=client)
    assert client.calls == ["identity", "reading", "webhook", "commands"]
    assert result["ordinary_message_access_verified"] is True
    assert result["group_privacy_disabled"] is True
    assert result["all_joined_groups_need_received_message_smoke"] is True


@pytest.mark.parametrize("client,reason", [
    (TelegramSetup(after_read=False), "group_reading_not_verified"),
    (TelegramSetup(after_id=BOT + 1), "identity_does_not_match"),
])
def test_failed_reading_or_identity_change_blocks_webhook_mutation(monkeypatch, client, reason):
    module = setup_admin(monkeypatch)
    with pytest.raises(module.CoachPreparationError, match=reason):
        module.configure_telegram(joined_config(), client=client)
    assert "webhook" not in client.calls and "commands" not in client.calls


def test_joined_setup_checks_optional_first_group_after_reading_policy(monkeypatch):
    module = setup_admin(monkeypatch)
    client = TelegramSetup()
    module.configure_telegram(joined_config(chat_id=CHAT), client=client)
    assert client.calls[:2] == ["identity", "reading"]
    assert client.calls[2] == ("group", {"chat_id": CHAT, "bot_user_id": BOT,
                                       "can_read_all_group_messages": True})
    assert client.calls[3:] == ["webhook", "commands"]


def test_joined_readiness_checks_identity_and_schema_before_first_group(monkeypatch):
    import app_v2.coach_main as module
    conn, identities = FakeConnection(), []
    monkeypatch.setattr(module, "CoachScopeRepository", lambda connection, *, bot_user_id:
                        SimpleNamespace(assert_identity=lambda: identities.append((connection, bot_user_id))))
    client = TestClient(module.create_app(joined_config(), connection_factory=lambda: conn))
    response = client.get("/ready")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert identities == [(conn, BOT)]
    assert any("coach_group_memberships" in sql for sql, _ in conn.queries)


def test_joined_readiness_does_not_hide_wrong_database_identity(monkeypatch):
    import app_v2.coach_main as module
    def wrong_identity():
        raise ValueError("synthetic wrong identity")
    monkeypatch.setattr(module, "CoachScopeRepository", lambda *a, **kw:
                        SimpleNamespace(assert_identity=wrong_identity))
    client = TestClient(module.create_app(joined_config(), connection_factory=FakeConnection))
    response = client.get("/ready")
    assert response.status_code == 503


def test_worker_pool_adds_and_evicts_scopes_without_rebuilding_live_workers(monkeypatch):
    import app_v2.coach_worker as module
    scopes, builds, runs = [], [], []
    class Registry:
        def __init__(self, *a, **kw):
            pass
        def assert_identity(self):
            pass
        def maintenance(self, **kwargs):
            pass
        def list_scopes(self, *, include_disabled=False):
            assert include_disabled  # control receipts must still drain in disabled topics
            return list(scopes)
    def build(conn, config, topic, *, chat_id, sender, provider):
        key = (chat_id, topic)
        builds.append((key, sender, provider))
        def run_once():
            runs.append(key)
            return key == (CHAT, 1)
        return SimpleNamespace(run_once=run_once)
    monkeypatch.setattr(module, "CoachScopeRepository", Registry)
    monkeypatch.setattr(module, "build_worker", build)
    sender, provider = object(), object()
    pool = module.CoachWorkerPool(FakeConnection(), joined_config(), sender=sender, provider=provider)
    assert not pool.run_once() and builds == []
    scopes.extend([(CHAT, 1), (CHAT, 17), (CHAT - 1, 1)])
    assert pool.run_once()
    first = dict(pool.workers)
    assert len(builds) == 3 and len(runs) == 3
    scopes.remove((CHAT, 17))
    scopes.append((CHAT - 1, 25))
    assert pool.run_once()
    assert len(builds) == 4
    assert pool.workers[(CHAT, 1)] is first[(CHAT, 1)]
    assert (CHAT, 17) not in pool.workers
    assert all(not worker.maintenance_enabled for worker in pool.workers.values())
    assert all(s is sender and p is provider for _, s, p in builds)
    scopes.clear()
    before = len(runs)
    assert not pool.run_once() and pool.workers == {} and len(runs) == before


def test_worker_pool_retains_configured_mode_allowlist(monkeypatch):
    import app_v2.coach_worker as module
    built = []
    registry = SimpleNamespace(assert_identity=lambda: None, maintenance=lambda **kw: None,
        list_scopes=lambda **kw: [(CHAT, 17), (CHAT, 19), (CHAT - 1, 17)])
    monkeypatch.setattr(module, "CoachScopeRepository", lambda *a, **kw: registry)
    def build(conn, config, topic, *, chat_id, **kwargs):
        built.append((chat_id, topic))
        return SimpleNamespace(run_once=lambda: False)
    monkeypatch.setattr(module, "build_worker", build)
    pool = module.CoachWorkerPool(FakeConnection(),
        joined_config(group_mode="configured", chat_id=CHAT, topic_ids=(17,)), sender=object())
    assert not pool.run_once()
    assert built == [(CHAT, 17)]


def test_worker_pool_cleans_global_transient_data_with_zero_active_groups(monkeypatch):
    import app_v2.coach_worker as module
    clock, cleaned = [100.0], []
    registry = SimpleNamespace(assert_identity=lambda: None, list_scopes=lambda **kw: [],
        maintenance=lambda **kw: cleaned.append(kw))
    monkeypatch.setattr(module, "CoachScopeRepository", lambda *a, **kw: registry)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    pool = module.CoachWorkerPool(FakeConnection(), joined_config(transient_ttl_seconds=3600), sender=object())
    assert not pool.run_once()
    assert cleaned == [{"transient_ttl_seconds": 3600}]
    clock[0] = 159.0
    assert not pool.run_once() and len(cleaned) == 1
    clock[0] = 160.0
    assert not pool.run_once() and len(cleaned) == 2
