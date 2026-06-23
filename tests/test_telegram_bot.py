from app.memory_store import InMemoryStore
from app.telegram_bot import (
    BOT_COMMANDS,
    BUTTON_CLEAR_GOAL,
    BUTTON_HELP,
    BUTTON_KICK,
    BUTTON_REPORT,
    BUTTON_SET_GOAL,
    MAIN_KEYBOARD,
    TelegramRuntimeState,
    build_reply,
    extract_text_message,
    run_startup_action,
)


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

    assert "Цель принята" in response
    assert store.get_goal(123) == "Запустить бота сегодня"


def test_goal_button_sets_pending_goal() -> None:
    store = InMemoryStore()
    runtime_state = TelegramRuntimeState()

    first_response = build_reply(123, BUTTON_SET_GOAL, store, runtime_state)
    second_response = build_reply(123, "Запустить бота сегодня до 20:00", store, runtime_state)

    assert "Напиши цель" in first_response
    assert "Цель принята" in second_response
    assert store.get_goal(123) == "Запустить бота сегодня до 20:00"


def test_build_reply_uses_chat_goal() -> None:
    store = InMemoryStore()
    store.set_goal(123, "Запустить бота сегодня")

    response = build_reply(123, "Потом сделаю", store)

    assert "Запустить бота сегодня" in response
    assert "Прокрастинация" in response


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
