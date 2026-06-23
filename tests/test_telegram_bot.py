from app.memory_store import InMemoryStore
from app.telegram_bot import build_reply, extract_text_message


def test_extract_text_message_reads_chat_and_text() -> None:
    update = {"message": {"chat": {"id": 123}, "text": "Потом сделаю"}}

    assert extract_text_message(update) == (123, "Потом сделаю")


def test_build_reply_sets_goal() -> None:
    store = InMemoryStore()

    response = build_reply(123, "/goal Запустить бота сегодня", store)

    assert "Цель принята" in response
    assert store.get_goal(123) == "Запустить бота сегодня"


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
