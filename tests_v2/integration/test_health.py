from types import SimpleNamespace

from fastapi.testclient import TestClient

from app_v2.config import AppConfig
from app_v2.main import app


client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "nenoy-v2-web"


def test_ready_returns_ready_in_default_dev_config() -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["service"] == "nenoy-v2-web"
    assert payload["environment"] in {"development", "test"}
    assert payload["database"] in {"not_configured", "ok"}


class Result:
    def fetchone(self): return (1,)


class FakeConn:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql):
        assert sql == "SELECT 1"
        return Result()


def test_ready_checks_database_when_configured(monkeypatch) -> None:
    import app_v2.main as main_module
    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(
            environment="test",
            app_name="НеНой 2.0",
            webhook_secret=None,
            database_url="postgresql://fake",
        ),
    )
    monkeypatch.setattr(main_module, "connect", lambda *args, **kwargs: FakeConn())

    response = TestClient(main_module.app).get("/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_ready_is_503_when_configured_database_is_down(monkeypatch) -> None:
    import app_v2.main as main_module
    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(
            environment="test",
            app_name="НеНой 2.0",
            webhook_secret=None,
            database_url="postgresql://fake",
        ),
    )
    def fail(*args, **kwargs):
        raise RuntimeError("db down")
    monkeypatch.setattr(main_module, "connect", fail)

    response = TestClient(main_module.app).get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "PostgreSQL not ready"


def test_production_boot_registers_current_telegram_webhook(monkeypatch) -> None:
    import app_v2.main as main_module

    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(
            environment="production",
            app_name="НеНой 2.0",
            webhook_secret="webhook-secret",
            telegram_bot_token="bot-token",
            telegram_bot_username="NeNoiBro_bot",
            database_url="postgresql://fake",
            openai_api_key="openai-key",
        ),
    )
    monkeypatch.setattr(
        main_module,
        "resolve_webhook_url",
        lambda: "https://bot.example.com/webhooks/telegram",
    )
    calls = []

    def fake_configure_webhook(*, token, secret, webhook_url):
        calls.append((token, secret, webhook_url))
        return SimpleNamespace(
            url=webhook_url,
            pending_update_count=0,
            last_error_message=None,
        )

    monkeypatch.setattr(main_module, "configure_webhook", fake_configure_webhook)

    main_module._configure_telegram_webhook_on_boot()

    assert calls == [
        (
            "bot-token",
            "webhook-secret",
            "https://bot.example.com/webhooks/telegram",
        )
    ]


def test_nonproduction_boot_does_not_touch_telegram(monkeypatch) -> None:
    import app_v2.main as main_module

    monkeypatch.setattr(
        main_module,
        "config",
        AppConfig(
            environment="test",
            app_name="НеНой 2.0",
            webhook_secret="webhook-secret",
            telegram_bot_token="bot-token",
        ),
    )
    calls = []
    monkeypatch.setattr(main_module, "configure_webhook", lambda **kwargs: calls.append(kwargs))

    main_module._configure_telegram_webhook_on_boot()

    assert calls == []
