from __future__ import annotations

from collections.abc import Callable

from app_v2.repositories.event_repo import ClaimedEvent, EventRepository


EventHandler = Callable[[ClaimedEvent], None]


class EventWorker:
    def __init__(
        self,
        repo: EventRepository,
        *,
        handler: EventHandler | None = None,
        processing_timeout_seconds: float = 120.0,
        max_attempts: int = 5,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
    ) -> None:
        self.repo = repo
        self.handler = handler or (lambda event: None)
        self.processing_timeout_seconds = processing_timeout_seconds
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    def run_once(self) -> bool:
        self.repo.recover_stale(
            max_attempts=self.max_attempts,
            base_delay_seconds=self.base_delay_seconds,
            max_delay_seconds=self.max_delay_seconds,
        )
        event = self.repo.claim_next(
            processing_timeout_seconds=self.processing_timeout_seconds
        )
        if event is None:
            return False

        try:
            self.handler(event)
        except Exception as exc:
            # A handler may fail after PostgreSQL has marked the shared
            # connection transaction as aborted. Retry bookkeeping itself then
            # cannot run until that transaction is rolled back.
            self.repo.rollback()
            self.repo.retry(
                event.event_id,
                str(exc),
                max_attempts=self.max_attempts,
                base_delay_seconds=self.base_delay_seconds,
                max_delay_seconds=self.max_delay_seconds,
            )
            return True

        self.repo.complete(event.event_id)
        return True
