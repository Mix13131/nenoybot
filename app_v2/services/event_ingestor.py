from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.adapters.postgres import connect
from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.repositories.ingest_repo import TelegramIngestRepository


@dataclass(frozen=True)
class IngestResult:
    status: str
    event_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"status": self.status, "event_id": self.event_id}


def ingest_telegram_update(
    update: dict[str, Any],
    database_url: str | None = None,
) -> IngestResult:
    normalized = normalize_update(update)
    if normalized is None:
        return IngestResult(status="ignored")

    with connect(database_url) as conn:
        repo = TelegramIngestRepository(conn)
        with conn.transaction():
            if repo.event_exists(normalized.telegram_update_id):
                return IngestResult(status="duplicate", event_id=normalized.envelope.event_id)

            user_id = repo.upsert_user(normalized.telegram_user)
            chat_id = repo.upsert_chat(normalized.telegram_chat)
            if normalized.is_group:
                repo.upsert_member(chat_id, user_id)
            repo.store_message(normalized, chat_id, user_id)
            inserted = repo.insert_event(normalized)

            if not inserted:
                return IngestResult(status="duplicate", event_id=normalized.envelope.event_id)

    return IngestResult(status="accepted", event_id=normalized.envelope.event_id)
