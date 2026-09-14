from __future__ import annotations

from app_v2.adapters.telegram_sender import TelegramSender
from app_v2.repositories.outbox_repo import OutboxRepository


class OutboxWorker:
    def __init__(
        self,
        repo: OutboxRepository,
        sender: TelegramSender,
        *,
        processing_timeout_seconds: float = 120.0,
        max_attempts: int = 5,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 300.0,
    ) -> None:
        self.repo = repo
        self.sender = sender
        self.processing_timeout_seconds = processing_timeout_seconds
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    def run_once(self) -> bool:
        self.repo.recover_stale()
        item = self.repo.claim_next(
            processing_timeout_seconds=self.processing_timeout_seconds
        )
        if item is None:
            return False

        if item.channel != "telegram":
            self.repo.retry(
                item.id,
                f"unsupported outbound channel: {item.channel}",
                max_attempts=self.max_attempts,
                base_delay_seconds=self.base_delay_seconds,
                max_delay_seconds=self.max_delay_seconds,
            )
            return True

        try:
            result = self.sender.send(item.destination_id, item.payload)
        except Exception as exc:
            self.repo.retry(
                item.id,
                str(exc),
                max_attempts=self.max_attempts,
                base_delay_seconds=self.base_delay_seconds,
                max_delay_seconds=self.max_delay_seconds,
            )
            return True

        telegram_message_id = result.get("message_id") if isinstance(result, dict) else None
        try:
            telegram_message_id = int(telegram_message_id) if telegram_message_id is not None else None
        except (TypeError, ValueError):
            telegram_message_id = None
        self.repo.mark_sent(item.id, telegram_message_id=telegram_message_id)
        return True
