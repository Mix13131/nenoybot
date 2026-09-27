from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


class TelegramProfileConfigurationError(RuntimeError):
    pass


class TelegramProfileLookupError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramBirthdate:
    day: int
    month: int
    year: int | None = None


@dataclass(frozen=True)
class TelegramBirthdateLookup:
    status: str
    birthdate: TelegramBirthdate | None = None


class TelegramProfileClient:
    """Small Bot API reader for privacy-visible user profile fields.

    The Bot API may omit birthdate or reject getChat for a group participant.
    Both outcomes are normal product states, not reasons to fail the group
    pipeline.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | Any | None = None,
        base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 6.0,
    ) -> None:
        resolved = (token or os.getenv("NENOY_V2_TELEGRAM_BOT_TOKEN") or "").strip()
        if not resolved:
            raise TelegramProfileConfigurationError(
                "Не задан NENOY_V2_TELEGRAM_BOT_TOKEN для Telegram profile reader"
            )
        self.token = resolved
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=max(1.0, timeout_seconds))

    def get_birthdate(self, telegram_user_id: str | int) -> TelegramBirthdateLookup:
        try:
            user_id = int(telegram_user_id)
        except (TypeError, ValueError) as exc:
            raise TelegramProfileLookupError("invalid_user_id") from exc

        try:
            response = self.client.post(
                f"{self.base_url}/bot{self.token}/getChat",
                json={"chat_id": user_id},
            )
        except Exception as exc:
            raise TelegramProfileLookupError(type(exc).__name__) from exc

        status_code = int(getattr(response, "status_code", 500) or 500)
        if status_code >= 500:
            raise TelegramProfileLookupError(f"telegram_http_{status_code}")

        try:
            data = response.json()
        except Exception as exc:
            raise TelegramProfileLookupError("invalid_json") from exc

        if status_code >= 400 or not data.get("ok"):
            # "chat not found" is expected for many group-only participants.
            return TelegramBirthdateLookup(status="unavailable")

        result = data.get("result")
        if not isinstance(result, dict):
            return TelegramBirthdateLookup(status="unavailable")

        raw = result.get("birthdate")
        if not isinstance(raw, dict):
            return TelegramBirthdateLookup(status="not_shared")

        try:
            day = int(raw.get("day"))
            month = int(raw.get("month"))
            year_raw = raw.get("year")
            year = int(year_raw) if year_raw is not None else None
            # 2000 deliberately permits February 29 when Telegram omits year.
            date(year or 2000, month, day)
        except (TypeError, ValueError):
            raise TelegramProfileLookupError("invalid_birthdate_payload")

        return TelegramBirthdateLookup(
            status="available",
            birthdate=TelegramBirthdate(day=day, month=month, year=year),
        )
