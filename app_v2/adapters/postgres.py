from __future__ import annotations

import os
from typing import Any


class DatabaseConfigurationError(RuntimeError):
    """Raised when the v2 database configuration is missing or invalid."""


def get_database_url(explicit_url: str | None = None) -> str:
    database_url = explicit_url or os.getenv("NENOY_V2_DATABASE_URL")
    if not database_url:
        raise DatabaseConfigurationError(
            "Не задан NENOY_V2_DATABASE_URL для PostgreSQL НеНой 2.0"
        )
    return database_url


def connect(database_url: str | None = None, **kwargs: Any):
    """Open a psycopg connection without coupling domain code to the driver."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - dependency is installed in runtime
        raise RuntimeError(
            "Для PostgreSQL runtime требуется dependency psycopg[binary]>=3.2.0"
        ) from exc

    return psycopg.connect(get_database_url(database_url), **kwargs)
