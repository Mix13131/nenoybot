from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class UserRow:
    chat_id: int
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    mode: str
    has_goal: bool
    schedule_enabled: bool
    message_count: int


class UserRegistry:
    def __init__(self, database_url: str | None, timezone_name: str = "Europe/Moscow") -> None:
        self.database_url = database_url
        self.timezone_name = timezone_name
        self.ensure_schema()

    def _connect(self):
        if not self.database_url:
            return None
        import psycopg

        return psycopg.connect(self.database_url, autocommit=True)

    def ensure_schema(self) -> None:
        if not self.database_url:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS nenoy_users (
                    chat_id BIGINT PRIMARY KEY,
                    telegram_user_id BIGINT,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    first_start_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_nenoy_users_last_seen_at
                ON nenoy_users (last_seen_at DESC)
                """
            )
            cursor.execute(
                """
                INSERT INTO nenoy_users (chat_id, first_seen_at, last_seen_at)
                SELECT chat_id, MIN(created_at), MAX(created_at)
                FROM nenoy_messages
                GROUP BY chat_id
                ON CONFLICT (chat_id) DO UPDATE SET
                    first_seen_at = LEAST(nenoy_users.first_seen_at, EXCLUDED.first_seen_at),
                    last_seen_at = GREATEST(nenoy_users.last_seen_at, EXCLUDED.last_seen_at),
                    updated_at = NOW()
                """
            )

    def track_private_message(self, update: dict) -> None:
        if not self.database_url:
            return
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(sender, dict):
            return
        chat_id = chat.get("id")
        telegram_user_id = sender.get("id")
        if not isinstance(chat_id, int):
            return

        text = message.get("text")
        is_start = isinstance(text, str) and text.strip().startswith("/start")
        now = datetime.now(UTC)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nenoy_users (
                    chat_id,
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                    language_code,
                    first_seen_at,
                    last_seen_at,
                    first_start_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    telegram_user_id = COALESCE(EXCLUDED.telegram_user_id, nenoy_users.telegram_user_id),
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    language_code = EXCLUDED.language_code,
                    last_seen_at = EXCLUDED.last_seen_at,
                    first_start_at = COALESCE(nenoy_users.first_start_at, EXCLUDED.first_start_at),
                    updated_at = NOW()
                """,
                (
                    chat_id,
                    telegram_user_id if isinstance(telegram_user_id, int) else None,
                    _text_or_none(sender.get("username")),
                    _text_or_none(sender.get("first_name")),
                    _text_or_none(sender.get("last_name")),
                    _text_or_none(sender.get("language_code")),
                    now,
                    now,
                    now if is_start else None,
                ),
            )

    def total_users(self) -> int:
        if not self.database_url:
            return 0
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM nenoy_users")
            return int(cursor.fetchone()[0])

    def recent_users(self, limit: int = 10) -> list[UserRow]:
        if not self.database_url:
            return []
        safe_limit = max(1, min(int(limit), 15))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.chat_id,
                    u.telegram_user_id,
                    u.username,
                    u.first_name,
                    u.last_name,
                    u.first_seen_at,
                    u.last_seen_at,
                    COALESCE(s.mode, 'coach') AS mode,
                    (COALESCE(NULLIF(BTRIM(st.goal), ''), '') <> '') AS has_goal,
                    COALESCE(s.enabled, FALSE) AS schedule_enabled,
                    COALESCE(mc.message_count, 0) AS message_count
                FROM nenoy_users u
                LEFT JOIN nenoy_support_settings s ON s.chat_id = u.chat_id
                LEFT JOIN nenoy_user_state st ON st.chat_id = u.chat_id
                LEFT JOIN (
                    SELECT chat_id, COUNT(*) AS message_count
                    FROM nenoy_messages
                    WHERE role LIKE 'user%'
                    GROUP BY chat_id
                ) mc ON mc.chat_id = u.chat_id
                ORDER BY u.last_seen_at DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            return [
                UserRow(
                    chat_id=int(row[0]),
                    telegram_user_id=int(row[1]) if row[1] is not None else None,
                    username=row[2],
                    first_name=row[3],
                    last_name=row[4],
                    first_seen_at=row[5],
                    last_seen_at=row[6],
                    mode=str(row[7]),
                    has_goal=bool(row[8]),
                    schedule_enabled=bool(row[9]),
                    message_count=int(row[10]),
                )
                for row in cursor.fetchall()
            ]

    def stats_text(self) -> str:
        if not self.database_url:
            return "Статистика пользователей недоступна: база данных не настроена."
        tz = ZoneInfo(self.timezone_name)
        now_local = datetime.now(tz)
        start_today_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        seven_days_ago = datetime.now(UTC) - timedelta(days=7)
        one_day_ago = datetime.now(UTC) - timedelta(days=1)

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE first_seen_at >= %s) AS new_today,
                    COUNT(*) FILTER (WHERE first_seen_at >= %s) AS new_7d,
                    COUNT(*) FILTER (WHERE last_seen_at >= %s) AS active_24h,
                    COUNT(*) FILTER (WHERE last_seen_at >= %s) AS active_7d
                FROM nenoy_users
                """,
                (start_today_utc, seven_days_ago, one_day_ago, seven_days_ago),
            )
            total, new_today, new_7d, active_24h, active_7d = map(int, cursor.fetchone())

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE COALESCE(s.mode, 'coach') = 'coach') AS coach_count,
                    COUNT(*) FILTER (WHERE COALESCE(s.mode, 'coach') = 'support') AS support_count,
                    COUNT(*) FILTER (WHERE COALESCE(s.enabled, FALSE)) AS schedule_count,
                    COUNT(*) FILTER (WHERE COALESCE(NULLIF(BTRIM(st.goal), ''), '') <> '') AS goal_count
                FROM nenoy_users u
                LEFT JOIN nenoy_support_settings s ON s.chat_id = u.chat_id
                LEFT JOIN nenoy_user_state st ON st.chat_id = u.chat_id
                """
            )
            coach_count, support_count, schedule_count, goal_count = map(int, cursor.fetchone())

        return (
            "📊 НеНойBot — пользователи\n\n"
            f"👥 Всего: {total}\n"
            f"🆕 Сегодня: {new_today}\n"
            f"🗓 За 7 дней: {new_7d}\n"
            f"🟢 Активны за 24 ч: {active_24h}\n"
            f"🟢 Активны за 7 дней: {active_7d}\n\n"
            f"🔥 Тренер: {coach_count}\n"
            f"🌿 Лёгкость: {support_count}\n"
            f"🎯 С целью: {goal_count}\n"
            f"🕒 С расписанием: {schedule_count}"
        )

    def users_text(self, limit: int = 10) -> str:
        rows = self.recent_users(limit)
        total = self.total_users()
        if not rows:
            return "👥 Пользователей пока нет."

        lines = [f"👥 Пользователей: {total}", f"Последние {len(rows)}:", ""]
        for index, row in enumerate(rows, start=1):
            name = display_name(row)
            username = f" · @{row.username}" if row.username and not name.startswith("@") else ""
            mode = "🌿 Лёгкость" if row.mode == "support" else "🔥 Тренер"
            goal = "✅" if row.has_goal else "—"
            schedule = "✅" if row.schedule_enabled else "—"
            lines.extend(
                [
                    f"{index}. {name}{username}",
                    f"   Первый вход: {format_dt(row.first_seen_at, self.timezone_name)}",
                    f"   Последняя активность: {format_dt(row.last_seen_at, self.timezone_name)}",
                    f"   {mode} · сообщений: {row.message_count} · цель: {goal} · расписание: {schedule}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()


def _text_or_none(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def display_name(row: UserRow) -> str:
    full_name = " ".join(part for part in (row.first_name, row.last_name) if part).strip()
    if full_name:
        return full_name
    if row.username:
        return f"@{row.username}"
    return "Пользователь без профиля"


def format_dt(value: datetime, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    local = value.astimezone(tz)
    now = datetime.now(tz)
    if local.date() == now.date():
        return f"сегодня {local:%H:%M}"
    if local.date() == (now.date() - timedelta(days=1)):
        return f"вчера {local:%H:%M}"
    return local.strftime("%d.%m.%Y %H:%M")
