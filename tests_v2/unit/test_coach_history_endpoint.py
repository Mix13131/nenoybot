"""History upload access is explicit, bounded, expiring and content-free in logs."""
from datetime import datetime, timedelta, timezone
import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app_v2.coach_config import CoachConfig
import app_v2.coach_history_endpoint as endpoint


PAYLOAD = b'{"messages": []}'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
SECRET = "synthetic_import_key_never_real_123456789"
HEADERS = {"X-Coach-Import-Secret": SECRET, "Content-Type": "application/json"}
ENV_NAMES = (
    "COACH_HISTORY_IMPORT_SECRET", "COACH_HISTORY_IMPORT_SHA256",
    "COACH_HISTORY_IMPORT_CHAT_ID", "COACH_HISTORY_IMPORT_EXPIRES_AT",
)


@pytest.fixture(autouse=True)
def clean_import_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, **overrides):
    values = {
        "COACH_HISTORY_IMPORT_SECRET": SECRET,
        "COACH_HISTORY_IMPORT_SHA256": DIGEST,
        "COACH_HISTORY_IMPORT_CHAT_ID": "-990101",
        "COACH_HISTORY_IMPORT_EXPIRES_AT": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def client(monkeypatch, *, importer=None):
    calls = []
    def run(config, payload, **kwargs):
        calls.append((config, payload, kwargs))
        if importer is not None:
            return importer(config, payload, **kwargs)
        return {
            "status": "preview" if kwargs["dry_run"] else "imported",
            "file_sha256": hashlib.sha256(payload).hexdigest(),
            "dry_run": kwargs["dry_run"],
            "counts": {"candidates": 3, "archived": 3, "skipped_service": 1,
                       "analysis_jobs_created": 0, "telegram_messages_sent": 0},
        }
    monkeypatch.setattr(endpoint, "import_history", run)
    config, factory = CoachConfig(), lambda: None
    app = FastAPI()
    endpoint.register_history_import(app, config, factory)
    return TestClient(app), calls, config, factory


def test_disabled_import_does_not_read_body_or_call_service(monkeypatch):
    http, calls, *_ = client(monkeypatch)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 404 and calls == []
    assert "/admin/history-import" not in http.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("name,value", [
    *[(name, None) for name in ENV_NAMES],
    ("COACH_HISTORY_IMPORT_SECRET", "short"),
    ("COACH_HISTORY_IMPORT_SECRET", "a secret with spaces" * 3),
    ("COACH_HISTORY_IMPORT_SHA256", "not-a-sha256"),
    ("COACH_HISTORY_IMPORT_CHAT_ID", "990101"),
    ("COACH_HISTORY_IMPORT_CHAT_ID", "0"),
    ("COACH_HISTORY_IMPORT_CHAT_ID", "-9223372036854775808"),
    ("COACH_HISTORY_IMPORT_EXPIRES_AT", "not-a-date"),
    ("COACH_HISTORY_IMPORT_EXPIRES_AT", "2100-01-01T00:00:00"),
    ("COACH_HISTORY_IMPORT_EXPIRES_AT", "2100-01-01T00:00:00+03:00"),
    ("COACH_HISTORY_IMPORT_EXPIRES_AT", "2000-01-01T00:00:00Z"),
])
def test_invalid_partial_or_expired_settings_fail_closed(monkeypatch, name, value):
    configure(monkeypatch, **{name: value})
    http, calls, *_ = client(monkeypatch)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 404 and calls == []


@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET + "extra"])
def test_import_requires_separate_authorization(monkeypatch, secret):
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    headers = {} if secret is None else {"X-Coach-Import-Secret": secret}
    response = http.post("/admin/history-import?apply=true", content=PAYLOAD, headers=headers)
    assert response.status_code == 401 and calls == []


@pytest.mark.parametrize("query,dry_run", [("", True), ("?apply=false", True), ("?apply=true", False)])
def test_preview_is_default_and_apply_is_explicit(monkeypatch, query, dry_run):
    configure(monkeypatch)
    http, calls, config, factory = client(monkeypatch)
    response = http.post("/admin/history-import" + query, content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == ("preview" if dry_run else "imported")
    assert result["dry_run"] is dry_run and result["file_sha256"] == DIGEST
    assert calls == [(config, PAYLOAD, {
        "expected_chat_id": -990101, "dry_run": dry_run, "connection_factory": factory,
    })]


def test_expiration_is_checked_again_after_application_creation(monkeypatch):
    configure(monkeypatch, COACH_HISTORY_IMPORT_EXPIRES_AT="2099-01-01T00:00:00Z")
    http, calls, *_ = client(monkeypatch)
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(endpoint, "datetime", FutureClock)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 404 and calls == []


def test_upload_cannot_apply_after_its_access_window_expires(monkeypatch):
    configure(monkeypatch, COACH_HISTORY_IMPORT_EXPIRES_AT="2099-01-01T00:00:00Z")
    http, calls, *_ = client(monkeypatch)
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    def upload():
        monkeypatch.setattr(endpoint, "datetime", FutureClock)
        yield PAYLOAD
    response = http.post("/admin/history-import?apply=true", content=upload(), headers=HEADERS)
    assert response.status_code == 404 and calls == []


@pytest.mark.parametrize("query", ["?apply=private-source-text", "?apply=true&apply=false"])
def test_invalid_mode_does_not_echo_input(monkeypatch, query):
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    response = http.post("/admin/history-import" + query, content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 400 and calls == []
    assert "private-source-text" not in response.text


def test_digest_mismatch_fails_before_importer(monkeypatch):
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    response = http.post("/admin/history-import", content=PAYLOAD + b" ", headers=HEADERS)
    assert response.status_code == 400 and calls == []
    assert response.json()["detail"] == "History export digest mismatch"


def test_declared_oversize_is_rejected_before_importer(monkeypatch):
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    response = http.post("/admin/history-import", content=PAYLOAD, headers={
        **HEADERS, "Content-Length": str(endpoint.MAX_HISTORY_BYTES + 1),
    })
    assert response.status_code == 413 and calls == []


@pytest.mark.parametrize("declared", [None, "1"])
def test_streaming_limit_cannot_be_bypassed_with_missing_or_false_length(monkeypatch, declared):
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    headers = dict(HEADERS)
    if declared is not None:
        headers["Content-Length"] = declared
    response = http.post("/admin/history-import", headers=headers,
                         content=iter([b"x" * endpoint.MAX_HISTORY_BYTES, b"x"]))
    assert response.status_code == 413 and calls == []


@pytest.mark.parametrize("exception,status", [(ValueError, 400), (RuntimeError, 503)])
def test_import_errors_never_disclose_source_or_credentials(monkeypatch, caplog, exception, status):
    configure(monkeypatch)
    private = "private-source-text postgresql://private_credentials"
    def fail(*args, **kwargs):
        raise exception(private)
    http, calls, *_ = client(monkeypatch, importer=fail)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == status and len(calls) == 1
    assert private not in response.text and private not in caplog.text
    assert SECRET not in caplog.text and PAYLOAD.decode() not in caplog.text


def test_incidental_service_data_is_not_returned_or_logged(monkeypatch, caplog):
    configure(monkeypatch)
    private = "private-source-text"
    def result(config, payload, **kwargs):
        return {"status": "preview", "dry_run": True, "file_sha256": DIGEST,
                "counts": {"archived": 1}, "private_message": private}
    http, *_ = client(monkeypatch, importer=result)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert private not in response.text and private not in caplog.text
    assert set(response.json()) == {"status", "dry_run", "file_sha256", "counts"}


@pytest.mark.parametrize("counts", [{"archived": "private-source-text"},
                                    {"private-source-text": 1}, {"archived": True}])
def test_non_counter_receipt_is_rejected_without_leaking_values(monkeypatch, caplog, counts):
    configure(monkeypatch)
    def result(config, payload, **kwargs):
        return {"status": "preview", "dry_run": True, "file_sha256": DIGEST, "counts": counts}
    http, *_ = client(monkeypatch, importer=result)
    response = http.post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 503
    assert "private-source-text" not in response.text + caplog.text


def test_coach_application_registers_the_explicit_import_route(monkeypatch):
    from app_v2.coach_main import create_app
    configure(monkeypatch)
    http, calls, *_ = client(monkeypatch)
    app = create_app(CoachConfig(), connection_factory=lambda: None)
    response = TestClient(app).post("/admin/history-import", content=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200 and len(calls) == 1
