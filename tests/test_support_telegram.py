from app.memory_store import InMemoryStore
from app.telegram_bot import BUTTON_FEEDBACK, TelegramRuntimeState, build_reply


def test_support_is_separate_and_does_not_clear_goal() -> None:
    store = InMemoryStore()
    state = TelegramRuntimeState()
    store.set_goal(1, "Сохранить это")
    state.wait_for_goal(1)
    reply = build_reply(1, "/support", store, state)
    assert "не нужно сдавать экзамен" in reply
    assert store.get_support_settings(1).mode == "support"
    assert store.get_goal(1) == "Сохранить это"
    assert 1 not in state.awaiting_goal
    assert "пинок" not in build_reply(1, "хорошо", store, state).casefold()


def test_feedback_requires_configured_care_chat(monkeypatch) -> None:
    store = InMemoryStore()
    state = TelegramRuntimeState()
    monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", None)
    assert "не настроена" in build_reply(1, BUTTON_FEEDBACK, store, state)
    assert 1 not in state.feedback_category


def test_feedback_preview_cancel_and_submit(monkeypatch) -> None:
    store = InMemoryStore()
    state = TelegramRuntimeState()
    monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", -100123)
    build_reply(1, BUTTON_FEEDBACK, store, state)
    preview = build_reply(1, "Расписание помогает", store, state)
    assert "Предпросмотр" in preview
    cancelled = build_reply(1, "/cancel", store, state)
    assert "не отправлено" in cancelled
    assert store.feedback == {}

    build_reply(1, BUTTON_FEEDBACK, store, state)
    build_reply(1, "Не хватает статуса", store, state)
    result = build_reply(1, "📨 Отправить", store, state)
    assert "сохранено" in result
    assert store.feedback[1].body == "Не хватает статуса"
    assert store.recent_messages(1) == []  # Ordinary conversation is not attached.


def test_exact_feedback_button_is_in_both_keyboards() -> None:
    from app.telegram_bot import MAIN_KEYBOARD, SUPPORT_KEYBOARD
    for keyboard in (MAIN_KEYBOARD, SUPPORT_KEYBOARD):
        labels = [button["text"] for row in keyboard["keyboard"] for button in row]
        assert BUTTON_FEEDBACK in labels
