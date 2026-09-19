from __future__ import annotations

from datetime import datetime, timezone

from app_v2.repositories.event_repo import ClaimedEvent, retry_delay_seconds
from app_v2.workers.event_worker import EventWorker


def _event(attempt_count: int = 1) -> ClaimedEvent:
    now = datetime.now(timezone.utc)
    return ClaimedEvent(
        id=1,
        event_id="evt-1",
        event_type="group_message",
        scope_type="group",
        scope_id="g1",
        actor_user_id="u1",
        payload={"text": "hi"},
        attempt_count=attempt_count,
        lease_until=now,
        created_at=now,
    )


class FakeRepo:
    def __init__(self, event: ClaimedEvent | None = None):
        self.event = event
        self.completed: list[str] = []
        self.retried: list[tuple[str, str]] = []
        self.recover_calls = 0
        self.claim_calls = 0
        self.rollback_calls = 0

    def recover_stale(self, **kwargs) -> int:
        self.recover_calls += 1
        return 0

    def claim_next(self, **kwargs):
        self.claim_calls += 1
        event, self.event = self.event, None
        return event

    def complete(self, event_id: str) -> bool:
        self.completed.append(event_id)
        return True

    def rollback(self) -> None:
        self.rollback_calls += 1

    def retry(self, event_id: str, error: str, **kwargs):
        self.retried.append((event_id, error))
        return "retry"


def test_worker_completes_successful_event() -> None:
    seen: list[str] = []
    repo = FakeRepo(_event())
    worker = EventWorker(repo, handler=lambda event: seen.append(event.event_id))

    assert worker.run_once() is True
    assert seen == ["evt-1"]
    assert repo.completed == ["evt-1"]
    assert repo.retried == []
    assert repo.recover_calls == 1


def test_worker_retries_failed_event() -> None:
    repo = FakeRepo(_event())

    def explode(event):
        raise RuntimeError("boom")

    worker = EventWorker(repo, handler=explode)

    assert worker.run_once() is True
    assert repo.completed == []
    assert repo.rollback_calls == 1
    assert repo.retried == [("evt-1", "boom")]


def test_worker_returns_false_when_queue_is_empty() -> None:
    repo = FakeRepo(None)
    worker = EventWorker(repo)

    assert worker.run_once() is False
    assert repo.claim_calls == 1


def test_retry_backoff_is_exponential_and_capped() -> None:
    assert retry_delay_seconds(1, base_delay_seconds=5, max_delay_seconds=300) == 5
    assert retry_delay_seconds(2, base_delay_seconds=5, max_delay_seconds=300) == 10
    assert retry_delay_seconds(3, base_delay_seconds=5, max_delay_seconds=300) == 20
    assert retry_delay_seconds(20, base_delay_seconds=5, max_delay_seconds=300) == 300
