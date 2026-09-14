from __future__ import annotations

from dataclasses import dataclass
import os


_ALLOWED_ENVIRONMENTS = {"development", "test", "production"}


class ConfigurationError(RuntimeError):
    """Raised when НеНой v2 configuration is invalid."""


@dataclass(frozen=True)
class AppConfig:
    environment: str
    app_name: str
    webhook_secret: str | None
    openai_api_key: str | None = None
    model_classifier: str = "gpt-5.6-luna"
    model_memory: str = "gpt-5.6-luna"
    model_generator: str = "gpt-5.6-terra"
    model_deep: str = "gpt-5.6-sol"
    openai_timeout_seconds: float = 30.0

    @property
    def service_name(self) -> str:
        return "nenoy-v2-web"


def _positive_float(source: dict[str, str] | os._Environ[str], name: str, default: float) -> float:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be > 0")
    return value


def load_config(environ: dict[str, str] | None = None) -> AppConfig:
    """Load v2 runtime configuration from environment variables.

    Test/development modes require no real credentials. Production requires the
    Telegram webhook secret so a production web process cannot start in an
    accidentally insecure state. OpenAI credentials remain optional until an AI
    adapter is actually invoked.
    """

    source = os.environ if environ is None else environ

    environment = (source.get("NENOY_V2_ENV") or "development").strip().lower()
    if environment not in _ALLOWED_ENVIRONMENTS:
        allowed = ", ".join(sorted(_ALLOWED_ENVIRONMENTS))
        raise ConfigurationError(
            f"Invalid NENOY_V2_ENV={environment!r}. Expected one of: {allowed}."
        )

    app_name = (source.get("NENOY_V2_APP_NAME") or "НеНой 2.0").strip()
    if not app_name:
        raise ConfigurationError("NENOY_V2_APP_NAME must not be empty.")

    webhook_secret = (source.get("NENOY_V2_WEBHOOK_SECRET") or "").strip() or None
    if environment == "production" and not webhook_secret:
        raise ConfigurationError(
            "NENOY_V2_WEBHOOK_SECRET is required when NENOY_V2_ENV=production."
        )

    def model(name: str, default: str) -> str:
        value = (source.get(name) or default).strip()
        if not value:
            raise ConfigurationError(f"{name} must not be empty")
        return value

    return AppConfig(
        environment=environment,
        app_name=app_name,
        webhook_secret=webhook_secret,
        openai_api_key=(source.get("NENOY_V2_OPENAI_API_KEY") or "").strip() or None,
        model_classifier=model("NENOY_V2_MODEL_CLASSIFIER", "gpt-5.6-luna"),
        model_memory=model("NENOY_V2_MODEL_MEMORY", "gpt-5.6-luna"),
        model_generator=model("NENOY_V2_MODEL_GENERATOR", "gpt-5.6-terra"),
        model_deep=model("NENOY_V2_MODEL_DEEP", "gpt-5.6-sol"),
        openai_timeout_seconds=_positive_float(
            source,
            "NENOY_V2_OPENAI_TIMEOUT_SECONDS",
            30.0,
        ),
    )
