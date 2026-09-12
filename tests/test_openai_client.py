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


def test_trainer_keeps_active_goal_when_user_relaxes() -> None:
    context = ConversationContext(
        goal="Доделать Content Hub и начать продавать",
        memory_summary="",
        recent_messages=(
            ("user", "Мне нужно доделать Content Hub и начать продавать"),
            ("assistant", "Принял."),
        ),
    )

    user_input = build_user_input("Я пока на расслабоне", context)

    assert "считай её активной" in user_input
    assert "расслабон" in user_input
    assert "прямо назови противоречие" in user_input
    assert "не уходи в бережный коучинг" in user_input


def test_trainer_user_input_rejects_lightness_language() -> None:
    context = ConversationContext(goal="Запустить продажи")

    user_input = build_user_input("Пока не хочу", context)

    assert "Не используй язык режима 🌿 Лёгкость" in user_input
    assert "без чувства вины" in user_input
    assert "если хочешь" in user_input
    assert "одна сильная мысль" in user_input
    assert "минимально достаточный ход" in user_input


def test_coach_uses_persona_v2_as_single_source_of_truth() -> None:
    instructions = build_system_instructions()

    assert "# НеНой Persona v2 — Trainer" in instructions
    assert "НеНой не спрашивает разрешения быть тренером" in instructions
    assert "Активная цель важнее новой отмазки" in instructions
    assert "НеНой не гладит отмазку по голове" in instructions


def test_lightness_prompt_stays_independent() -> None:
    instructions = build_support_instructions()

    assert "# НеНойBot — режим поддержки" in instructions
    assert "Не требуй цель, отчёт, дедлайн или обязательное действие" in instructions
    assert "НеНой Persona v2" not in instructions
