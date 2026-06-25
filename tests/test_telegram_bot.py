from app.memory_store import InMemoryStore
from app.reminders import build_reminder_message
from app.telegram_bot import (
    BOT_COMMANDS,
    BUTTON_CLEAR_GOAL,
    BUTTON_HELP,
    BUTTON_KICK,
    BUTTON_REPORT,
    BUTTON_SET_GOAL,
    STYLE_GUARD_FALLBACK,
    MAIN_KEYBOARD,
    TelegramAPI,
    TelegramRuntimeState,
    prepare_outgoing_text,
    build_reply,
    extract_text_message,
    schedule_reminder_if_found,
    run_startup_action,
)
from app.style_guard import find_forbidden_style_phrases, is_human_style_response
from pathlib import Path


def test_bot_commands_include_start_and_goal() -> None:
    assert {"command": "start", "description": "Запустить НеНойBot"} in BOT_COMMANDS
    assert any(command["command"] == "goal" for command in BOT_COMMANDS)


def test_main_keyboard_contains_action_buttons() -> None:
    buttons = [button["text"] for row in MAIN_KEYBOARD["keyboard"] for button in row]

    assert BUTTON_SET_GOAL in buttons
    assert BUTTON_REPORT in buttons
    assert BUTTON_KICK in buttons
    assert BUTTON_HELP in buttons
    assert BUTTON_CLEAR_GOAL in buttons


def test_startup_action_does_not_raise_on_runtime_error() -> None:
    def fail() -> None:
        raise RuntimeError("boom")

    run_startup_action("test", fail)


def test_extract_text_message_reads_chat_and_text() -> None:
    update = {"message": {"chat": {"id": 123}, "text": "Потом сделаю"}}

    assert extract_text_message(update) == (123, "Потом сделаю")


def test_build_reply_sets_goal() -> None:
    store = InMemoryStore()

    response = build_reply(123, "/goal Запустить бота сегодня", store)

    assert "выполняем" in response
    assert "первый шаг" in response
    assert store.get_goal(123) == "Запустить бота сегодня"


def test_goal_button_sets_pending_goal() -> None:
    store = InMemoryStore()
    runtime_state = TelegramRuntimeState()

    first_response = build_reply(123, BUTTON_SET_GOAL, store, runtime_state)
    second_response = build_reply(123, "Запустить бота сегодня до 20:00", store, runtime_state)

    assert "Напиши цель" in first_response
    assert "первый шаг" in second_response
    assert store.get_goal(123) == "Запустить бота сегодня до 20:00"


def test_build_reply_uses_chat_goal() -> None:
    store = InMemoryStore()
    store.set_goal(123, "Запустить бота сегодня")

    response = build_reply(123, "Потом сделаю", store)

    assert "Запустить бота сегодня" in response
    assert ("Первый шаг" in response) or ("первый шаг" in response)


def test_build_reply_clears_goal() -> None:
    store = InMemoryStore()
    store.set_goal(123, "Запустить бота сегодня")

    response = build_reply(123, "/clear_goal", store)

    assert "Цель сброшена" in response
    assert store.get_goal(123) is None


def test_clear_goal_button_clears_goal() -> None:
    store = InMemoryStore()
    store.set_goal(123, "Запустить бота сегодня")

    response = build_reply(123, BUTTON_CLEAR_GOAL, store, TelegramRuntimeState())

    assert "Цель сброшена" in response
    assert store.get_goal(123) is None


def test_build_reply_schedules_goal_reminder() -> None:
    store = InMemoryStore()

    response = build_reply(123, "/goal Проверить API завтра в 12:00", store)

    assert "Вернешься с результатом" in response
    assert len(store.reminders) == 1


def test_schedule_reminder_ignores_text_without_due_time() -> None:
    store = InMemoryStore()

    response = schedule_reminder_if_found(123, "Проверить API когда-нибудь", store)

    assert response is None
    assert store.reminders == {}


def test_schedule_reminder_records_separate_times_and_confirms_fix() -> None:
    store = InMemoryStore()

    response = schedule_reminder_if_found(
        123,
        "Напоминание в 20:40, отчёт в 20:45, после этого wind down в 21:00",
        store,
    )

    assert response is not None
    assert "Фиксирую" in response
    assert "20:45" in response
    assert "20:40" in response
    assert len(store.reminders) == 2


def test_schedule_reminder_uses_active_checkin_time_for_checkin() -> None:
    store = InMemoryStore()

    schedule_reminder_if_found(
        123,
        "Задача до 22:00, напоминание в 20:40, отчёт в 20:45, после этого wind down в 21:00",
        store,
    )

    reminders = list(store.reminders.values())
    assert len(reminders) == 2
    assert any(reminder.reminder_type == "reminder" for reminder in reminders)
    assert any(reminder.reminder_type == "checkin" for reminder in reminders)
    assert not any(reminder.reminder_type == "task" for reminder in reminders)

    checkin_due = next(reminder.due_at for reminder in reminders if reminder.reminder_type == "checkin")
    assert checkin_due.hour == 20
    assert checkin_due.minute == 45


def test_schedule_reminder_updates_previous_times_for_same_task() -> None:
    store = InMemoryStore()

    schedule_reminder_if_found(
        123,
        "Отчёт в 20:45, напоминание в 20:40, после этого wind down в 21:00",
        store,
    )
    first_times = sorted((reminder.due_at for reminder in store.reminders.values()), key=lambda value: value)

    schedule_reminder_if_found(
        123,
        "Отчёт в 20:35, напоминание в 20:30, после этого wind down в 20:50",
        store,
    )
    second_times = sorted((reminder.due_at for reminder in store.reminders.values()), key=lambda value: value)

    assert len(store.reminders) == 2
    assert len(first_times) == 2
    assert first_times != second_times
    assert second_times[0].hour == 20
    assert second_times[0].minute == 30
    assert second_times[1].hour == 20
    assert second_times[1].minute == 35


def test_prepare_outgoing_text_replaces_bot_like_reply() -> None:
    fallback = prepare_outgoing_text(123, "Запрос принят.")

    assert fallback == STYLE_GUARD_FALLBACK


def test_start_reply_stays_human_style() -> None:
    store = InMemoryStore()
    reply = build_reply(123, "/start", store)

    assert is_human_style_response(reply)
    assert not find_forbidden_style_phrases(reply)


def test_send_message_is_guarded_for_direct_text() -> None:
    api = TelegramAPI("stub")

    captured: dict[str, str] = {}

    def fake_request(method: str, payload: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["text"] = payload["text"]
        return {"ok": True, "result": {}}

    api.request = fake_request  # type: ignore[method-assign]
    api.send_message(123, "Запрос принят.")

    assert captured["method"] == "sendMessage"
    assert captured["text"] == STYLE_GUARD_FALLBACK


def test_send_guarded_message_passes_reminder_text() -> None:
    api = TelegramAPI("stub")
    captured: dict[str, str] = {}

    def fake_request(method: str, payload: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["text"] = payload["text"]
        return {"ok": True, "result": {}}

    api.request = fake_request  # type: ignore[method-assign]
    api.send_guarded_message(123, build_reminder_message("Проверить API"))

    assert captured["method"] == "sendMessage"
    reminder_text = build_reminder_message("Проверить API")
    assert captured["text"] == reminder_text
    assert not find_forbidden_style_phrases(reminder_text)


def test_no_callback_text_path_without_guard() -> None:
    telegram_bot_source = Path(__file__).resolve().parents[1] / "app" / "telegram_bot.py"
    source_text = telegram_bot_source.read_text(encoding="utf-8")

    assert "answer_callback_query" not in source_text
