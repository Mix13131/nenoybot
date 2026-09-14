from fastapi.testclient import TestClient

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
