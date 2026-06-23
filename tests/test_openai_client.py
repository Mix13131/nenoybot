from app.openai_client import ConversationContext, build_user_input


def test_build_user_input_contains_goal_memory_and_message() -> None:
    context = ConversationContext(
        goal="Запустить Telegram-бота",
        memory_summary="Пользователь часто откладывает старт.",
        recent_messages=(("user", "Потом"), ("assistant", "Покажи шаг.")),
    )

    user_input = build_user_input("Мне лень", context)

    assert "Запустить Telegram-бота" in user_input
    assert "Пользователь часто откладывает старт." in user_input
    assert "user: Потом" in user_input
    assert "Мне лень" in user_input
