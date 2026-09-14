from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app_v2.adapters.telegram_sender import (
    TelegramSendError,
    TelegramSender,
    TelegramSenderConfigurationError,
)
from app_v2.repositories.outbox_repo import ClaimedOutbox, retry_delay_seconds
from app_v2.workers.outbox_worker import OutboxWorker


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict):
        self.calls.append((url, json))
        return self.response


def _claimed(channel: str = "telegram") -> ClaimedOutbox:
    return ClaimedOutbox(
        id=1,
        dedupe_key="reply:event-1",
        channel=channel,
        destination_id="-100123",
        payload={"text": "Привет", "reply_to_message_id": "77"},
        attempt_count=1,
        lease_until=datetime.now(timezone.utc),
    )


def test_sender_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NENOY_V2_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(TelegramSenderConfigurationError, match="NENOY_V2_TELEGRAM_BOT_TOKEN"):
        TelegramSender()


def test_sender_passes_text_and_reply_target() -> None:
    client = FakeClient(FakeResponse(200, {"ok": True, "result": {"message_id": 88}}))
    sender = TelegramSender(token="123:test", client=client, base_url="https://example.test")

    result = sender.send("-100123", {"text": "Привет", "reply_to_message_id": "77"})

    assert result["message_id"] == 88
    url, payload = client.calls[0]
    assert url.endswith("/bot123:test/sendMessage")
    assert payload["chat_id"] == "-100123"
    assert payload["text"] == "Привет"
    assert payload["reply_parameters"] == {"message_id": 77}


def test_sender_raises_on_telegram_api_error() -> None:
    client = FakeClient(FakeResponse(200, {"ok": False, "description": "Bad Request"}))
    sender = TelegramSender(token="123:test", client=client)

    with pytest.raises(TelegramSendError, match="Bad Request"):
        sender.send("1", {"text": "hello"})


class FakeRepo:
    def __init__(self, item: ClaimedOutbox | None = None) -> None:
        self.item = item
        self.sent: list[tuple[int, int | None]] = []
        self.retried: list[tuple[int, str]] = []
        self.recover_calls = 0

    def recover_stale(self) -> int:
        self.recover_calls += 1
        return 0

    def claim_next(self, **kwargs):
        item, self.item = self.item, None
        return item

    def mark_sent(self, outbox_id: int, *, telegram_message_id: int | None = None) -> bool:
        self.sent.append((outbox_id, telegram_message_id))
        return True

    def retry(self, outbox_id: int, error: str, **kwargs):
        self.retried.append((outbox_id, error))
        return "retry"


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def send(self, destination_id: str, payload: dict):
        self.calls.append((destination_id, payload))
        if self.error:
            raise self.error
        return {"message_id": 88}


def test_outbox_worker_marks_successful_send_sent_and_persists_telegram_message_id() -> None:
    repo = FakeRepo(_claimed())
    sender = FakeSender()
    worker = OutboxWorker(repo, sender)

    assert worker.run_once() is True
    assert repo.sent == [(1, 88)]
    assert repo.retried == []
    assert sender.calls[0][0] == "-100123"


def test_outbox_worker_retries_failed_send() -> None:
    repo = FakeRepo(_claimed())
    sender = FakeSender(TelegramSendError("network down"))
    worker = OutboxWorker(repo, sender)

    assert worker.run_once() is True
    assert repo.sent == []
    assert repo.retried == [(1, "network down")]


def test_outbox_worker_retries_unsupported_channel() -> None:
    repo = FakeRepo(_claimed(channel="email"))
    worker = OutboxWorker(repo, FakeSender())

    assert worker.run_once() is True
    assert repo.retried[0][0] == 1
    assert "unsupported outbound channel" in repo.retried[0][1]


def test_outbox_retry_backoff_is_capped() -> None:
    assert retry_delay_seconds(1, base_delay_seconds=5, max_delay_seconds=300) == 5
    assert retry_delay_seconds(2, base_delay_seconds=5, max_delay_seconds=300) == 10
    assert retry_delay_seconds(20, base_delay_seconds=5, max_delay_seconds=300) == 300
