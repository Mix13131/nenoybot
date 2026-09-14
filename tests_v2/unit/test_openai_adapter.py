from __future__ import annotations

from types import SimpleNamespace

import pytest

from app_v2.adapters.openai_adapter import (
    OpenAIAdapter,
    OpenAIAdapterConfigurationError,
    OpenAIAdapterError,
)
from app_v2.config import AppConfig
from app_v2.services.cost_tracker import estimate_cost_usd
from app_v2.services.model_router import ModelRole, ModelRouter


def _config(**kwargs) -> AppConfig:
    values = {
        "environment": "test",
        "app_name": "НеНой 2.0",
        "webhook_secret": None,
    }
    values.update(kwargs)
    return AppConfig(**values)


class FakeUsageRepo:
    def __init__(self) -> None:
        self.records = []

    def record(self, usage) -> int:
        self.records.append(usage)
        return len(self.records)


class FakeResponses:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def _response(output_text: str = "hello", model: str = "gpt-5.6-luna"):
    return SimpleNamespace(
        output_text=output_text,
        model=model,
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            input_tokens_details=SimpleNamespace(cached_tokens=200),
        ),
    )


def test_model_router_uses_configured_models() -> None:
    config = _config(
        model_classifier="classifier-model",
        model_memory="memory-model",
        model_generator="generator-model",
        model_deep="deep-model",
    )
    router = ModelRouter(config)

    assert router.route(ModelRole.CLASSIFIER).model == "classifier-model"
    assert router.route(ModelRole.MEMORY).model == "memory-model"
    assert router.route(ModelRole.GENERATOR).model == "generator-model"
    assert router.route(ModelRole.DEEP).model == "deep-model"
    assert router.route(ModelRole.CLASSIFIER).reasoning_effort == "low"
    assert router.route(ModelRole.DEEP).reasoning_effort == "high"


def test_cost_estimate_accounts_for_cached_tokens() -> None:
    # Luna: 800 uncached * .20/M + 200 cached * .02/M + 500 output * 1.20/M.
    expected = (800 * 0.20 + 200 * 0.02 + 500 * 1.20) / 1_000_000
    assert estimate_cost_usd(
        "gpt-5.6-luna",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=200,
    ) == round(expected, 10)


def test_adapter_requires_key_without_injected_client() -> None:
    with pytest.raises(OpenAIAdapterConfigurationError, match="NENOY_V2_OPENAI_API_KEY"):
        OpenAIAdapter(_config(openai_api_key=None))


def test_text_call_captures_usage_and_request_shape() -> None:
    fake_responses = FakeResponses(_response())
    usage_repo = FakeUsageRepo()
    adapter = OpenAIAdapter(
        _config(),
        client=FakeClient(fake_responses),
        usage_repo=usage_repo,
    )

    result = adapter.generate_text(
        ModelRole.CLASSIFIER,
        "classify this",
        instructions="Return a short answer",
        event_id="evt-1",
    )

    assert result.text == "hello"
    assert result.parsed is None
    assert result.usage.success is True
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 500
    assert result.usage.cached_tokens == 200
    assert result.usage.event_id == "evt-1"
    assert len(usage_repo.records) == 1

    request = fake_responses.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["instructions"] == "Return a short answer"


def test_structured_call_sends_strict_json_schema_and_parses_json() -> None:
    fake_responses = FakeResponses(_response('{"banter_score":0.8}'))
    adapter = OpenAIAdapter(_config(), client=FakeClient(fake_responses))
    schema = {
        "type": "object",
        "properties": {"banter_score": {"type": "number"}},
        "required": ["banter_score"],
        "additionalProperties": False,
    }

    result = adapter.generate_json(
        ModelRole.CLASSIFIER,
        "analyze",
        schema_name="scene_analysis",
        schema=schema,
    )

    assert result.parsed == {"banter_score": 0.8}
    fmt = fake_responses.calls[0]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "scene_analysis"
    assert fmt["schema"] == schema
    assert fmt["strict"] is True


def test_failed_call_records_failed_usage_and_raises_useful_error() -> None:
    fake_responses = FakeResponses(error=TimeoutError("timeout"))
    usage_repo = FakeUsageRepo()
    adapter = OpenAIAdapter(
        _config(),
        client=FakeClient(fake_responses),
        usage_repo=usage_repo,
    )

    with pytest.raises(OpenAIAdapterError, match="classifier.*gpt-5.6-luna.*timeout"):
        adapter.generate_text(ModelRole.CLASSIFIER, "hello", event_id="evt-fail")

    assert len(usage_repo.records) == 1
    failed = usage_repo.records[0]
    assert failed.success is False
    assert failed.event_id == "evt-fail"
    assert failed.input_tokens == 0
    assert failed.output_tokens == 0
