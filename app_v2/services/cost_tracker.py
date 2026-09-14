from __future__ import annotations

from dataclasses import dataclass

PRICING_VERSION = "2026-09-14"


@dataclass(frozen=True)
class TokenPricing:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


# Snapshot used for internal COGS estimates. Unknown models intentionally price at
# zero rather than breaking a user-facing AI call; monitoring can flag them.
MODEL_PRICING_USD_PER_MILLION: dict[str, TokenPricing] = {
    "gpt-5.6-luna": TokenPricing(0.20, 0.02, 1.20),
    "gpt-5.6-terra": TokenPricing(2.00, 0.20, 12.00),
    "gpt-5.6-sol": TokenPricing(4.00, 0.40, 20.00),
    "gpt-5.6": TokenPricing(4.00, 0.40, 20.00),
}


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float:
    pricing = MODEL_PRICING_USD_PER_MILLION.get(model)
    if pricing is None:
        return 0.0

    cached = max(0, min(cached_tokens, input_tokens))
    uncached = max(0, input_tokens - cached)
    total = (
        uncached * pricing.input_per_million
        + cached * pricing.cached_input_per_million
        + output_tokens * pricing.output_per_million
    ) / 1_000_000
    return round(total, 10)
