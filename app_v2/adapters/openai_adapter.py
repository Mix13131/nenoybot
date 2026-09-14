from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app_v2.config import AppConfig
from app_v2.domain.usage import LLMUsageRecord
from app_v2.services.cost_tracker import estimate_cost_usd
from app_v2.services.model_router import ModelRole, ModelRouter


class OpenAIAdapterConfigurationError(RuntimeError):
    pass


class OpenAIAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIResult:
    text: str
    parsed: dict[str, Any] | None
    usage: LLMUsageRecord


def _usage_numbers(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return input_tokens, output_tokens, cached_tokens


class OpenAIAdapter:
    def __init__(
        self,
        config: AppConfig,
        *,
        usage_repo: Any | None = None,
        client: Any | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        self.config = config
        self.usage_repo = usage_repo
        self.router = router or ModelRouter(config)

        if client is not None:
            self.client = client
            return

        if not config.openai_api_key:
            raise OpenAIAdapterConfigurationError(
                "Не задан NENOY_V2_OPENAI_API_KEY для OpenAI adapter НеНой 2.0"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise OpenAIAdapterConfigurationError(
                "Для OpenAI runtime требуется dependency openai>=2.0.0"
            ) from exc
        self.client = OpenAI(
            api_key=config.openai_api_key,
            timeout=config.openai_timeout_seconds,
            max_retries=0,
        )

    def generate_text(
        self,
        role: ModelRole,
        input_text: str,
        *,
        instructions: str | None = None,
        event_id: str | None = None,
        intervention_id: str | None = None,
        max_output_tokens: int | None = None,
    ) -> OpenAIResult:
        return self._generate(
            role,
            input_text,
            instructions=instructions,
            event_id=event_id,
            intervention_id=intervention_id,
            max_output_tokens=max_output_tokens,
            schema_name=None,
            schema=None,
        )

    def generate_json(
        self,
        role: ModelRole,
        input_text: str,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str | None = None,
        event_id: str | None = None,
        intervention_id: str | None = None,
        max_output_tokens: int | None = None,
    ) -> OpenAIResult:
        if not schema_name.strip():
            raise ValueError("schema_name must not be empty")
        return self._generate(
            role,
            input_text,
            instructions=instructions,
            event_id=event_id,
            intervention_id=intervention_id,
            max_output_tokens=max_output_tokens,
            schema_name=schema_name,
            schema=schema,
        )

    def _generate(
        self,
        role: ModelRole,
        input_text: str,
        *,
        instructions: str | None,
        event_id: str | None,
        intervention_id: str | None,
        max_output_tokens: int | None,
        schema_name: str | None,
        schema: dict[str, Any] | None,
    ) -> OpenAIResult:
        route = self.router.route(role)
        started = time.perf_counter()
        response: Any | None = None
        input_tokens = output_tokens = cached_tokens = 0
        success = False

        kwargs: dict[str, Any] = {
            "model": route.model,
            "input": input_text,
            "store": False,
            "reasoning": {"effort": route.reasoning_effort},
        }
        if instructions:
            kwargs["instructions"] = instructions
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if schema_name is not None and schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            }

        try:
            response = self.client.responses.create(**kwargs)
            input_tokens, output_tokens, cached_tokens = _usage_numbers(response)
            text = str(getattr(response, "output_text", "") or "")
            parsed = json.loads(text) if schema is not None else None
            success = True
        except Exception as exc:
            if response is not None:
                input_tokens, output_tokens, cached_tokens = _usage_numbers(response)
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            usage = self._build_usage(
                role,
                route.model,
                event_id,
                intervention_id,
                input_tokens,
                output_tokens,
                cached_tokens,
                latency_ms,
                False,
            )
            self._record_usage(usage)
            raise OpenAIAdapterError(
                f"OpenAI {role.value} call failed for model {route.model}: {exc}"
            ) from exc

        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        actual_model = str(getattr(response, "model", None) or route.model)
        usage = self._build_usage(
            role,
            actual_model,
            event_id,
            intervention_id,
            input_tokens,
            output_tokens,
            cached_tokens,
            latency_ms,
            success,
        )
        self._record_usage(usage)
        return OpenAIResult(text=text, parsed=parsed, usage=usage)

    def _build_usage(
        self,
        role: ModelRole,
        model: str,
        event_id: str | None,
        intervention_id: str | None,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        latency_ms: int,
        success: bool,
    ) -> LLMUsageRecord:
        return LLMUsageRecord(
            usage_id=f"usage_{uuid.uuid4().hex}",
            event_id=event_id,
            intervention_id=intervention_id,
            task_kind=role.value,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            estimated_cost_usd=estimate_cost_usd(
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
            ),
            latency_ms=latency_ms,
            success=success,
            created_at=datetime.now(timezone.utc),
        )

    def _record_usage(self, usage: LLMUsageRecord) -> None:
        if self.usage_repo is not None:
            self.usage_repo.record(usage)
