from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LabReaction:
    emoji: str
    count: int


@dataclass(frozen=True)
class LabMedia:
    media_type: str | None = None
    mime_type: str | None = None
    file_extension: str | None = None
    file_size: int | None = None
    duration_seconds: int | None = None
    width: int | None = None
    height: int | None = None
    sticker_emoji: str | None = None


@dataclass(frozen=True)
class CanonicalLabEvent:
    source_message_id: int
    event_type: str
    occurred_at: datetime
    actor_alias: str | None
    actor_role: str | None
    text: str
    reply_to_source_message_id: int | None = None
    edited_at: datetime | None = None
    service_action: str | None = None
    forwarded_actor_alias: str | None = None
    media: LabMedia | None = None
    reactions: tuple[LabReaction, ...] = ()
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["edited_at"] = self.edited_at.isoformat() if self.edited_at else None
        return data


@dataclass(frozen=True)
class CanonicalLabDataset:
    dataset_id: str
    label: str
    source_kind: str
    source_chat_type: str
    events: tuple[CanonicalLabEvent, ...]
    stats: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset_id": self.dataset_id,
            "label": self.label,
            "source_kind": self.source_kind,
            "source_chat_type": self.source_chat_type,
            "events": [event.as_dict() for event in self.events],
            "stats": dict(self.stats),
        }
