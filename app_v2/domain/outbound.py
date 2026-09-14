from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import DomainModel, NonEmptyStr
from .enums import ScopeType


class OutboundMessage(DomainModel):
    message_id: NonEmptyStr
    scope_type: ScopeType
    scope_id: NonEmptyStr
    text: NonEmptyStr
    reply_to_message_id: str | None = None
    dedupe_key: NonEmptyStr
    metadata: dict[str, Any] = Field(default_factory=dict)
