from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .base import DomainModel, InterventionScore
from .enums import PrimaryAction, ReasonCode, ResponseMode, SecondaryAction


class DispatcherDecision(DomainModel):
    primary_action: PrimaryAction
    secondary_actions: list[SecondaryAction] = Field(default_factory=list)
    mode: ResponseMode | None = None
    intervention_score: InterventionScore | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    target_user_id: str | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reply_mode(self) -> "DispatcherDecision":
        if self.primary_action is PrimaryAction.REPLY and self.mode is None:
            raise ValueError("mode is required when primary_action=reply")
        return self
