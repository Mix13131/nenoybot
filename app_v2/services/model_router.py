from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app_v2.config import AppConfig


class ModelRole(str, Enum):
    CLASSIFIER = "classifier"
    MEMORY = "memory"
    GENERATOR = "generator"
    DEEP = "deep"


@dataclass(frozen=True)
class ModelRoute:
    role: ModelRole
    model: str
    reasoning_effort: str


class ModelRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def route(self, role: ModelRole) -> ModelRoute:
        if role is ModelRole.CLASSIFIER:
            return ModelRoute(role, self.config.model_classifier, "low")
        if role is ModelRole.MEMORY:
            return ModelRoute(role, self.config.model_memory, "low")
        if role is ModelRole.GENERATOR:
            return ModelRoute(role, self.config.model_generator, "medium")
        if role is ModelRole.DEEP:
            return ModelRoute(role, self.config.model_deep, "high")
        raise ValueError(f"Unsupported model role: {role}")
