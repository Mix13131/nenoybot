from app.openai_client import (
    ConversationContext,
    build_system_instructions,
    build_support_instructions,
    build_user_input,
)


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


def test_build_user_input_does_not_force_action_every_time() -> None:
    context = ConversationContext(
        goal="Сделать webhook",
        memory_summary="Пользователь устал.",
        recent_messages=(("user", "я устал"),),
    )

    user_input = build_user_input("давай просто поболтаем", context)

    assert "Не тащи пользователя к действию автоматически" in user_input
    assert "Если действие не нужно — не придумывай его" in user_input
    assert "минимально достаточный взрослый ход" in user_input


def test_coach_uses_persona_v2_as_single_source_of_truth() -> None:
    instructions = build_system_instructions()

    assert "# НеНой Persona v2" in instructions
    assert "Действие — инструмент, а не религия" in instructions
    assert "Не превращай нормальный человеческий разговор в тренировку" in instructions


def test_lightness_prompt_stays_independent() -> None:
    instructions = build_support_instructions()

    assert "# НеНойBot — режим поддержки" in instructions
    assert "Не требуй цель, отчёт, дедлайн или обязательное действие" in instructions
    assert "# НеНой Persona v2" not in instructions
