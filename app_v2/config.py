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

    @property
    def service_name(self) -> str:
        return "nenoy-v2-web"


def load_config(environ: dict[str, str] | None = None) -> AppConfig:
    """Load v2 runtime configuration from environment variables.

    Test/development modes require no real credentials. Production requires the
    future Telegram webhook secret so a production process cannot start in an
    accidentally insecure state.
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

    return AppConfig(
        environment=environment,
        app_name=app_name,
        webhook_secret=webhook_secret,
    )
