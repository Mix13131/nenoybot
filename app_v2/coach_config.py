"""Explicit configuration for the separately deployed, single-bot coach.

No legacy environment fallback: accidentally pointing the coach at NeNoi's
credentials must not turn an unrelated deployment into the pilot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
import re
from typing import Mapping


class CoachConfigurationError(ValueError):
    """Configuration key/reason only, never a supplied credential."""


@dataclass(frozen=True)
class CoachConfig:
    environment: str = "development"
    enabled: bool = False
    ai_enabled: bool = False
    database_url: str | None = field(default=None, repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    bot_user_id: int | None = None
    bot_username: str | None = None
    display_name: str = "Напарник"
    aliases: tuple[str, ...] = ("напарник",)
    chat_id: int | None = None
    topic_ids: tuple[int, ...] = ()
    archive_policy: str | None = None
    transient_ttl_seconds: int = 86400
    public_base_url: str | None = None
    ai_model: str | None = None
    daily_cap_microusd: int | None = None
    input_price_microusd_per_million: int | None = None
    output_price_microusd_per_million: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    ai_timeout_seconds: int = 40

    def permits_scope(self, chat_id: int, topic_id: int) -> bool:
        return chat_id == self.chat_id and topic_id in self.topic_ids

    def ai_config(self):
        from app_v2.services.coach_ai import CoachAIConfig
        if not self.ai_enabled:
            raise CoachConfigurationError("COACH_AI_ENABLED is off")
        return CoachAIConfig(
            model=self.ai_model,
            daily_cap_microusd=self.daily_cap_microusd,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            input_price_microusd_per_million=self.input_price_microusd_per_million,
            output_price_microusd_per_million=self.output_price_microusd_per_million,
            timeout_seconds=self.ai_timeout_seconds,
        )


def _bool(env: Mapping[str, str], key: str) -> bool:
    value = env.get(key, "0").strip().lower()
    if value not in {"0", "1", "false", "true"}:
        raise CoachConfigurationError(f"{key}: expected 0 or 1")
    return value in {"1", "true"}


def _integer(env: Mapping[str, str], key: str, *, minimum=1, maximum=2**63 - 1,
             default: int | None = None) -> int | None:
    value = env.get(key, "").strip()
    if not value:
        return default
    try:
        result = int(value)
    except ValueError as exc:
        raise CoachConfigurationError(f"{key}: expected integer") from None
    if not minimum <= result <= maximum:
        raise CoachConfigurationError(f"{key}: outside allowed range")
    return result


def _money(env: Mapping[str, str], key: str) -> int | None:
    value = env.get(key, "").strip()
    if not value:
        return None
    try:
        number = Decimal(value)
        micro = number * 1_000_000
        if not number.is_finite() or micro != micro.to_integral_value() or not 0 < micro <= 10**15:
            raise ValueError
        return int(micro)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise CoachConfigurationError(f"{key}: positive USD amount with at most 6 decimals required") from None


def load_coach_config(env: Mapping[str, str] | None = None) -> CoachConfig:
    env = os.environ if env is None else env
    get = lambda key: (env.get(key) or "").strip() or None
    environment = get("COACH_ENV") or "development"
    if environment not in {"development", "test", "production"}:
        raise CoachConfigurationError("COACH_ENV: unknown environment")
    enabled, ai_enabled = _bool(env, "COACH_ENABLED"), _bool(env, "COACH_AI_ENABLED")
    token = get("COACH_TELEGRAM_BOT_TOKEN")
    bot_id = _integer(env, "COACH_BOT_USER_ID")
    if token:
        token_match = re.fullmatch(r"([1-9][0-9]{0,18}):[A-Za-z0-9_-]{20,}", token)
        if token_match is None:
            raise CoachConfigurationError("COACH_TELEGRAM_BOT_TOKEN: invalid format")
        token_id = int(token_match.group(1))
        if token_id >= 2**63:
            raise CoachConfigurationError("COACH_TELEGRAM_BOT_TOKEN: invalid identity")
        if bot_id is not None and bot_id != token_id:
            raise CoachConfigurationError("COACH_BOT_USER_ID: token identity mismatch")
        bot_id = token_id
    username = (get("COACH_BOT_USERNAME") or "").lstrip("@") or None
    if username and not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise CoachConfigurationError("COACH_BOT_USERNAME: invalid username")
    chat_id = _integer(env, "COACH_CHAT_ID", minimum=-(2**63 - 1), maximum=-1)
    topic_ids: tuple[int, ...] = ()
    if get("COACH_TOPIC_IDS"):
        tokens = env["COACH_TOPIC_IDS"].split(",")
        if len(tokens) > 20:
            raise CoachConfigurationError("COACH_TOPIC_IDS: at most 20 explicit scopes")
        try:
            topic_ids = tuple(sorted({int(item.strip()) for item in tokens}))
            if any(item <= 0 or item >= 2**63 for item in topic_ids):
                raise ValueError
        except ValueError as exc:
            raise CoachConfigurationError("COACH_TOPIC_IDS: positive forum IDs required") from None
    display_name = get("COACH_DISPLAY_NAME") or "Напарник"
    aliases = tuple(dict.fromkeys(item.strip().casefold() for item in
                    (get("COACH_ALIASES") or display_name).split(",") if item.strip()))
    if len(display_name) > 64 or any(len(x) > 64 for x in aliases) or len(aliases) > 8:
        raise CoachConfigurationError("COACH_DISPLAY_NAME/COACH_ALIASES: too long")
    policy = get("COACH_ARCHIVE_POLICY")
    if policy is not None and policy != "until_author_deletes":
        raise CoachConfigurationError("COACH_ARCHIVE_POLICY: unsupported explicit policy")
    secret = get("COACH_WEBHOOK_SECRET")
    if secret and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        raise CoachConfigurationError("COACH_WEBHOOK_SECRET: 32–256 safe characters required")
    base_url = get("COACH_PUBLIC_BASE_URL")
    if base_url and (not base_url.startswith("https://") or any(x in base_url for x in ("?", "#", "@"))):
        raise CoachConfigurationError("COACH_PUBLIC_BASE_URL: public HTTPS origin required")
    config = CoachConfig(
        environment=environment, enabled=enabled, ai_enabled=ai_enabled,
        database_url=get("COACH_DATABASE_URL"), telegram_bot_token=token,
        webhook_secret=secret, openai_api_key=get("COACH_OPENAI_API_KEY"),
        bot_user_id=bot_id, bot_username=username, display_name=display_name, aliases=aliases,
        chat_id=chat_id, topic_ids=topic_ids, archive_policy=policy,
        transient_ttl_seconds=_integer(env, "COACH_TRANSIENT_TTL_SECONDS", minimum=60, maximum=86400, default=86400),
        public_base_url=base_url, ai_model=get("COACH_AI_MODEL"),
        daily_cap_microusd=_money(env, "COACH_AI_EMERGENCY_DAILY_USD"),
        input_price_microusd_per_million=_money(env, "COACH_AI_INPUT_USD_PER_MILLION"),
        output_price_microusd_per_million=_money(env, "COACH_AI_OUTPUT_USD_PER_MILLION"),
        max_input_tokens=_integer(env, "COACH_AI_MAX_INPUT_TOKENS", maximum=500_000),
        max_output_tokens=_integer(env, "COACH_AI_MAX_OUTPUT_TOKENS", maximum=32_000),
        ai_timeout_seconds=_integer(env, "COACH_AI_TIMEOUT_SECONDS", maximum=60, default=40),
    )
    if environment == "production" or enabled:
        required = {
            "COACH_DATABASE_URL": config.database_url,
            "COACH_TELEGRAM_BOT_TOKEN": token, "COACH_WEBHOOK_SECRET": secret,
            "COACH_BOT_USERNAME": username, "COACH_CHAT_ID": chat_id,
            "COACH_TOPIC_IDS": topic_ids, "COACH_ARCHIVE_POLICY": policy,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise CoachConfigurationError("Missing " + ", ".join(missing))
    if ai_enabled:
        required = {
            "COACH_OPENAI_API_KEY": config.openai_api_key, "COACH_AI_MODEL": config.ai_model,
            "COACH_AI_EMERGENCY_DAILY_USD": config.daily_cap_microusd,
            "COACH_AI_INPUT_USD_PER_MILLION": config.input_price_microusd_per_million,
            "COACH_AI_OUTPUT_USD_PER_MILLION": config.output_price_microusd_per_million,
            "COACH_AI_MAX_INPUT_TOKENS": config.max_input_tokens,
            "COACH_AI_MAX_OUTPUT_TOKENS": config.max_output_tokens,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise CoachConfigurationError("Missing " + ", ".join(missing))
    return config
