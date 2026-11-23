"""One pre-reserved JSON gateway for every paid Arni model operation.

Prices/cap/model are explicit deployment inputs, never guessed from a model
name. All inputs are bounded plain text; tools, files, images, stored provider
conversations and implicit retries are unsupported. Output bounds use Responses
max_output_tokens, including reasoning: https://developers.openai.com/api/docs/guides/token-counting.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import time
from typing import Any, Mapping, Protocol, Sequence

from app_v2.repositories.coach_budget_repo import (
    CoachBudgetConfigurationError,
    CoachBudgetExceeded,
    CoachBudgetRepository,
    CoachOperationAlreadyAttempted,
    token_cost_microusd,
)


class CoachAIConfigurationError(ValueError):
    pass


class CoachAIError(RuntimeError):
    pass


_JSON_INSTRUCTIONS = 'Return exactly one JSON object. Follow the supplied JSON schema when present.'
_ENV_FIELDS = {
    'model': 'NENOY_V2_COACH_AI_MODEL',
    'daily_cap_microusd': 'NENOY_V2_COACH_AI_DAILY_CAP_MICROUSD',
    'max_input_tokens': 'NENOY_V2_COACH_AI_MAX_INPUT_TOKENS',
    'max_output_tokens': 'NENOY_V2_COACH_AI_MAX_OUTPUT_TOKENS',
    'input_price_microusd_per_million': 'NENOY_V2_COACH_AI_INPUT_PRICE_MICROUSD_PER_MILLION',
    'output_price_microusd_per_million': 'NENOY_V2_COACH_AI_OUTPUT_PRICE_MICROUSD_PER_MILLION',
    'timeout_seconds': 'NENOY_V2_COACH_AI_TIMEOUT_SECONDS',
}


@dataclass(frozen=True)
class CoachAIConfig:
    model: str
    daily_cap_microusd: int
    max_input_tokens: int
    max_output_tokens: int
    input_price_microusd_per_million: int
    output_price_microusd_per_million: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 160:
            raise CoachAIConfigurationError('An explicitly priced model is required')
        for field in ('daily_cap_microusd', 'max_input_tokens', 'max_output_tokens',
                      'input_price_microusd_per_million', 'output_price_microusd_per_million'):
            value = getattr(self, field)
            if type(value) is not int or not 0 < value < (1 << 63):
                raise CoachAIConfigurationError(f'{field} must be an explicit positive integer')
        if self.max_input_tokens >= (1 << 31) or self.max_output_tokens >= (1 << 31):
            raise CoachAIConfigurationError('Configured token bounds exceed the supported range')
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise CoachAIConfigurationError('timeout_seconds must be finite and between 0 and 300')
        # Verify integer overflow now, before a request can reach the provider.
        try:
            self.max_cost_microusd
        except CoachBudgetConfigurationError:
            raise CoachAIConfigurationError('Configured price/bounds exceed the supported range') from None

    @property
    def max_cost_microusd(self) -> int:
        return token_cost_microusd(self.max_input_tokens, self.max_output_tokens,
                                  self.input_price_microusd_per_million, self.output_price_microusd_per_million)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'CoachAIConfig':
        source = os.environ if environ is None else environ
        missing = [name for name in _ENV_FIELDS.values() if not source.get(name, '').strip()]
        if missing:
            raise CoachAIConfigurationError('Missing explicit coach AI configuration: ' + ', '.join(missing))
        values = {}
        try:
            for field, name in _ENV_FIELDS.items():
                raw = source[name].strip()
                values[field] = raw if field == 'model' else float(raw) if field == 'timeout_seconds' else int(raw)
        except (ValueError, TypeError):
            raise CoachAIConfigurationError('Coach AI configuration contains an invalid numeric value') from None
        return cls(**values)


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str
    status: str = 'completed'


class JsonProvider(Protocol):
    def complete_json(self, *, model: str, messages: list[dict[str, str]], schema: dict | None,
                      max_output_tokens: int, timeout_seconds: float) -> ProviderResponse: ...


@dataclass(frozen=True)
class CoachAIResult:
    """Known token usage cost at the explicit tariff, without guessed cache discounts."""
    parsed: dict[str, Any]
    raw_text: str
    input_tokens: int
    output_tokens: int
    cost_microusd: int


def _json_object(text: str) -> dict:
    def no_constants(value):
        raise ValueError('Non-finite JSON number')
    def unique_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('Duplicate JSON key')
            obj[key] = value
        return obj
    parsed = json.loads(text, parse_constant=no_constants, object_pairs_hook=unique_keys)
    if not isinstance(parsed, dict):
        raise ValueError('Expected a JSON object')
    return parsed


class CoachAI:
    def __init__(self, budget: CoachBudgetRepository, provider: JsonProvider, config: CoachAIConfig) -> None:
        if not isinstance(config, CoachAIConfig):
            raise CoachAIConfigurationError('Explicit validated AI configuration is required')
        self.budget, self.provider, self.config = budget, provider, config

    def _prepare(self, messages: Sequence[dict[str, str]], schema: dict | None) -> tuple[list[dict], dict | None, str, int]:
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 64:
            raise CoachAIConfigurationError('Supply between 1 and 64 plain-text messages')
        for message in messages:
            if not isinstance(message, dict) or set(message) != {'role', 'content'}:
                raise CoachAIConfigurationError('Only explicit role/content text messages are supported')
            if message['role'] not in {'system', 'developer', 'user', 'assistant'}:
                raise CoachAIConfigurationError('Unsupported message role')
            if not isinstance(message['content'], str) or not message['content'].strip():
                raise CoachAIConfigurationError('Message content must be nonempty plain text')
        if schema is not None and (not isinstance(schema, dict) or schema.get('type') != 'object'):
            raise CoachAIConfigurationError('The requested JSON schema must describe an object')
        try:
            canonical = json.dumps({'messages': messages, 'schema': schema, 'instructions': _JSON_INSTRUCTIONS},
                                   ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            raw_bytes = canonical.encode('utf-8')
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise CoachAIConfigurationError('The request is not a bounded serializable text payload') from None
        # Byte-level allowance, intentionally more conservative than a guessed
        # chars/token ratio: 4 tokens per serialized UTF-8 byte plus framing.
        # Includes schema/instructions; no tools/media/history IDs can add input.
        # Provider usage beyond the configured bound trips the monetary breaker.
        upper_bound = 4 * len(raw_bytes) + 4096 + 128 * len(messages)
        if upper_bound > self.config.max_input_tokens:
            raise CoachAIConfigurationError('Request exceeds the configured conservative input-token bound')
        frozen_payload = json.loads(canonical)
        return frozen_payload['messages'], frozen_payload['schema'], canonical, upper_bound

    def estimate_request(self, *, messages: Sequence[dict[str, str]], schema: dict | None = None) -> dict:
        """Read-only dry-run: no connection, reservation, API call or guessed price."""
        _, _, _, input_upper_bound = self._prepare(messages, schema)
        return dict(input_upper_bound=input_upper_bound, max_input_tokens=self.config.max_input_tokens,
                    max_output_tokens=self.config.max_output_tokens, max_cost_microusd=self.config.max_cost_microusd)

    def _uncertain(self, operation_id: str, *, outcome: str, latency_ms: int, block_day: bool = False) -> None:
        try:
            self.budget.mark_uncertain(operation_id, outcome=outcome, latency_ms=latency_ms, block_day=block_day)
        except Exception:
            # The original committed reservation remains held even if telemetry
            # storage becomes unavailable. Never hide this by retrying/refunding.
            pass

    def request_json(self, *, operation_id: str, purpose: str, messages: Sequence[dict[str, str]],
                     schema: dict | None = None) -> CoachAIResult:
        prepared, prepared_schema, canonical, _ = self._prepare(messages, schema)
        cfg = self.config
        request_hash = hashlib.sha256(json.dumps({
            'payload': canonical, 'purpose': purpose, 'model': cfg.model,
            'max_output_tokens': cfg.max_output_tokens,
        }, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        try:
            self.budget.reserve(
                operation_id=operation_id, purpose=purpose, model=cfg.model, request_hash=request_hash,
                daily_cap_microusd=cfg.daily_cap_microusd, max_input_tokens=cfg.max_input_tokens,
                max_output_tokens=cfg.max_output_tokens,
                input_price_microusd_per_million=cfg.input_price_microusd_per_million,
                output_price_microusd_per_million=cfg.output_price_microusd_per_million,
            )
        except (CoachBudgetExceeded, CoachOperationAlreadyAttempted, CoachBudgetConfigurationError):
            raise
        except Exception:
            raise CoachAIError('Budget persistence unavailable; AI was not called') from None
        started = time.monotonic()
        try:
            response = self.provider.complete_json(
                model=cfg.model, messages=prepared, schema=prepared_schema,
                max_output_tokens=cfg.max_output_tokens, timeout_seconds=cfg.timeout_seconds,
            )
        except Exception:
            latency = min((1 << 31) - 1, max(0, int((time.monotonic() - started) * 1000)))
            self._uncertain(operation_id, outcome='provider_error', latency_ms=latency)
            raise CoachAIError('Provider request failed; the monetary reservation remains held') from None
        latency = min((1 << 31) - 1, max(0, int((time.monotonic() - started) * 1000)))
        if not isinstance(response, ProviderResponse) or response.model != cfg.model:
            self._uncertain(operation_id, outcome='unknown_model', latency_ms=latency, block_day=True)
            raise CoachAIError('Provider returned an unpriced model; the monetary reservation remains held')
        if any(type(value) is not int or value < 0 for value in (response.input_tokens, response.output_tokens)):
            self._uncertain(operation_id, outcome='usage_missing', latency_ms=latency)
            raise CoachAIError('Provider usage is unknown; the monetary reservation remains held')
        overrun = response.input_tokens > cfg.max_input_tokens or response.output_tokens > cfg.max_output_tokens
        parsed = None
        try:
            parsed = _json_object(response.text)
        except (ValueError, TypeError, RecursionError):
            pass
        outcome = 'invalid_json' if parsed is None else 'silence' if str(parsed.get('action', parsed.get('decision', ''))).lower() == 'silence' else 'valid_json'
        if response.status != 'completed':
            outcome = 'incomplete'
        try:
            cost = self.budget.reconcile(operation_id, input_tokens=response.input_tokens,
                                         output_tokens=response.output_tokens, outcome=outcome, latency_ms=latency)
        except Exception:
            raise CoachAIError('Usage reconciliation unavailable; the existing reservation must be investigated') from None
        if overrun:
            raise CoachAIError('Provider usage exceeded its configured bound; the monetary breaker is blocked')
        if parsed is None or response.status != 'completed':
            raise CoachAIError('Provider returned no completed valid JSON object; usage was recorded')
        return CoachAIResult(parsed, response.text, response.input_tokens, response.output_tokens, cost)


class OpenAIResponsesProvider:
    """Explicit live transport, one HTTP POST, no retries or provider-side history.

    Instantiate only after explicit pilot activation. Tests inject MockTransport;
    no real call is made by constructing this object or importing the module.
    """

    def __init__(self, api_key: str, *, transport=None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise CoachAIConfigurationError('An explicit API key is required')
        self._api_key = api_key
        self._transport = transport

    def complete_json(self, *, model: str, messages: list[dict[str, str]], schema: dict | None,
                      max_output_tokens: int, timeout_seconds: float) -> ProviderResponse:
        import httpx

        payload = dict(model=model, input=messages, instructions=_JSON_INSTRUCTIONS,
                       max_output_tokens=max_output_tokens, store=False, truncation='disabled',
                       tools=[], background=False, stream=False)
        payload['text'] = {'format': {'type': 'json_object'} if schema is None else {
            'type': 'json_schema', 'name': 'coach_response', 'schema': schema, 'strict': True,
        }}
        transport = self._transport if self._transport is not None else httpx.HTTPTransport(retries=0)
        with httpx.Client(transport=transport, timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.post('https://api.openai.com/v1/responses', json=payload,
                                   headers={'Authorization': 'Bearer ' + self._api_key})
            response.raise_for_status()
            body = response.json()
        usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
        texts = [part['text'] for item in body.get('output', []) if isinstance(item, dict) and item.get('type') == 'message'
                 for part in item.get('content', []) if isinstance(part, dict) and part.get('type') == 'output_text' and isinstance(part.get('text'), str)]
        return ProviderResponse(''.join(texts), usage.get('input_tokens'), usage.get('output_tokens'),
                                body.get('model', ''), body.get('status', 'unknown'))
