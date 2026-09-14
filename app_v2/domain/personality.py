from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import DomainModel, PersonalityLevel
from .enums import ResponseMode


class PersonalityState(DomainModel):
    mode: ResponseMode
    directness: PersonalityLevel
    brevity: PersonalityLevel
    warmth: PersonalityLevel
    pressure: PersonalityLevel
    humor: PersonalityLevel
    sarcasm: PersonalityLevel
    roast: PersonalityLevel
    profanity_level: PersonalityLevel
    profanity_frequency: PersonalityLevel
    initiative: PersonalityLevel
    callback: PersonalityLevel
    challenge: PersonalityLevel
    care: PersonalityLevel
    playfulness: PersonalityLevel
    sensitivity: PersonalityLevel
    metadata: dict[str, Any] = Field(default_factory=dict)
