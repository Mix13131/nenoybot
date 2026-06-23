from app.memory_store import InMemoryStore


def test_in_memory_store_keeps_goal_and_messages() -> None:
    store = InMemoryStore()

    store.set_goal(123, "Запустить бота")
    store.append_message(123, "user", "Потом")
    store.append_message(123, "assistant", "Делай")

    assert store.get_goal(123) == "Запустить бота"
    assert store.recent_messages(123) == [("user", "Потом"), ("assistant", "Делай")]


def test_in_memory_store_clears_goal() -> None:
    store = InMemoryStore()

    store.set_goal(123, "Запустить бота")
    store.clear_goal(123)

    assert store.get_goal(123) is None
