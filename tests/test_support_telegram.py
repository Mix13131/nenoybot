from datetime import UTC, datetime, timedelta

from app.feedback import FeedbackDraft
from app.memory_store import InMemoryStore
from app.telegram_bot import (
    BUTTON_FEEDBACK, BUTTON_NEED_HELP, BUTTON_OBSERVATION, TelegramDeliveryError,
    FEEDBACK_KEYBOARD, TelegramRuntimeState, build_reply, deliver_care_reply,
    deliver_pending_feedback, deliver_telegram, requires_private_chat,
)


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
    assert store.get_feedback_draft(1) is None


def test_feedback_preview_cancel_and_submit(monkeypatch) -> None:
    store = InMemoryStore()
    state = TelegramRuntimeState()
    monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", -100123)
    build_reply(1, BUTTON_FEEDBACK, store, state)
    assert BUTTON_OBSERVATION in build_reply(1, BUTTON_FEEDBACK, store, state)
    build_reply(1, BUTTON_OBSERVATION, store, state)
    preview = build_reply(1, "Расписание помогает", store, state)
    assert "Предпросмотр" in preview
    cancelled = build_reply(1, "/cancel", store, state)
    assert "не отправлено" in cancelled
    assert store.feedback == {}

    build_reply(1, BUTTON_FEEDBACK, store, state)
    build_reply(1, BUTTON_NEED_HELP, store, state)
    build_reply(1, "Не хватает статуса", store, state)
    result = build_reply(1, "📨 Отправить", store, state)
    assert "сохранено" in result
    assert store.feedback[1].body == "Не хватает статуса"
    assert store.feedback[1].category == "help"
    assert store.recent_messages(1) == []  # Ordinary conversation is not attached.


def test_category_buttons_are_only_in_dedicated_feedback_keyboard() -> None:
    from app.telegram_bot import MAIN_KEYBOARD, SUPPORT_KEYBOARD
    for keyboard in (MAIN_KEYBOARD, SUPPORT_KEYBOARD):
        labels = [button["text"] for row in keyboard["keyboard"] for button in row]
        assert BUTTON_FEEDBACK in labels
        assert BUTTON_OBSERVATION not in labels
        assert BUTTON_NEED_HELP not in labels
    feedback_labels = [button["text"] for row in FEEDBACK_KEYBOARD["keyboard"] for button in row]
    assert BUTTON_OBSERVATION in feedback_labels
    assert BUTTON_NEED_HELP in feedback_labels


def test_feedback_draft_survives_runtime_restart_and_expires(monkeypatch) -> None:
    store = InMemoryStore()
    monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", -100123)
    build_reply(1, BUTTON_FEEDBACK, store, TelegramRuntimeState())
    build_reply(1, BUTTON_OBSERVATION, store, TelegramRuntimeState())
    preview = build_reply(1, "Черновик", store, TelegramRuntimeState())
    assert "Предпросмотр" in preview
    assert build_reply(1, "/feedback_edit", store, TelegramRuntimeState()) == "Пришли исправленный текст."
    assert store.get_feedback_draft(1).body is None
    store.feedback_drafts[1] = FeedbackDraft(1, "observation", "старый", datetime.now(UTC) - timedelta(hours=25))
    assert store.get_feedback_draft(1) is None


def test_delivery_classifies_429_403_and_ambiguous() -> None:
    calls = 0

    def rate_limited_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TelegramDeliveryError("rate_limited", "slow down", 7)
        return 42
    sleeps = []
    assert deliver_telegram(rate_limited_once, sleeps.append) == ("confirmed", 42)
    assert sleeps == [7]
    assert deliver_telegram(lambda: (_ for _ in ()).throw(TelegramDeliveryError("blocked", "forbidden"))) == ("blocked", None)
    assert deliver_telegram(lambda: (_ for _ in ()).throw(RuntimeError("socket timeout"))) == ("uncertain", None)


def test_groups_keep_coach_but_reject_personal_support() -> None:
    store = InMemoryStore()
    assert not requires_private_chat("group", -10, "Обычный отчёт", store)
    assert requires_private_chat("group", -10, "/support", store)
    assert requires_private_chat("group", -10, BUTTON_FEEDBACK, store)


class RecordingAPI:
    def __init__(self, outcomes=()):
        self.outcomes = iter(outcomes)
        self.sent = []

    def _send_raw_message(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text))
        outcome = next(self.outcomes, "confirmed")
        if outcome != "confirmed":
            raise TelegramDeliveryError(outcome, outcome)
        return len(self.sent)


def test_confirmed_care_notification_acknowledges_user_only_after_delivery(monkeypatch) -> None:
    store = InMemoryStore()
    feedback = store.create_feedback(1, "help", "Нужна помощь", "support", None)
    monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", -100123)
    api = RecordingAPI()
    deliver_pending_feedback(api, store)
    assert store.get_feedback(feedback.id).notification_status == "confirmed"
    assert api.sent[-1] == (1, f"Обращение №{feedback.id} передано команде. Ответ появится здесь.")


def test_unconfirmed_care_notification_does_not_claim_delivery(monkeypatch) -> None:
    for outcome in ("blocked", "rejected", "uncertain"):
        store = InMemoryStore()
        feedback = store.create_feedback(1, "help", "Нужна помощь", "support", None)
        monkeypatch.setattr("app.telegram_bot.AppConfig.care_chat_id", -100123)
        api = RecordingAPI((outcome,))
        deliver_pending_feedback(api, store)
        assert store.get_feedback(feedback.id).notification_status == outcome
        assert len(api.sent) == 1


def test_care_reply_marks_replied_only_after_confirmed_delivery() -> None:
    for outcome in ("confirmed", "blocked", "rejected", "uncertain"):
        store = InMemoryStore()
        feedback = store.create_feedback(1, "help", "Нужна помощь", "support", None)
        status = deliver_care_reply(RecordingAPI((outcome,)), store, feedback, "Ответ")
        saved = store.get_feedback(feedback.id)
        assert status == outcome
        assert saved.status == ("replied" if outcome == "confirmed" else "submitted")
        assert (saved.replied_at is not None) is (outcome == "confirmed")
