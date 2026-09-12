from datetime import UTC, datetime

from app.user_registry import UserRow, display_name, format_dt


def _row(**overrides) -> UserRow:
    values = dict(
        chat_id=1,
        telegram_user_id=1,
        username="anton",
        first_name="Anton",
        last_name="T",
        first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 1, 2, tzinfo=UTC),
        mode="coach",
        has_goal=True,
        schedule_enabled=False,
        message_count=10,
    )
    values.update(overrides)
    return UserRow(**values)


def test_display_name_prefers_real_name() -> None:
    assert display_name(_row()) == "Anton T"


def test_display_name_uses_username_without_name() -> None:
    assert display_name(_row(first_name=None, last_name=None)) == "@anton"


def test_display_name_has_safe_fallback() -> None:
    assert display_name(_row(first_name=None, last_name=None, username=None)) == "Пользователь без профиля"


def test_format_dt_uses_explicit_timezone() -> None:
    rendered = format_dt(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), "Europe/Moscow")
    assert rendered.endswith("15:00")
