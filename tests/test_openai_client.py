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
    assert "поймай противоречие" in user_input
    assert "ПОДЪЁБ → ПРАВДА → ХОД" in user_input


def test_trainer_user_input_has_v3_sarcasm_contract() -> None:
    context = ConversationContext(goal="Запустить продажи")

    user_input = build_user_input("Пока не хочу", context)

    assert "Persona v3" in user_input
    assert "Ной не ныл — и ты не ной" in user_input
    assert "примерно 7/10" in user_input
    assert "Лёгкий буллинг" in user_input
    assert "ХОД не обязателен" in user_input
    assert "не скатывайся в бережный коучинг" in user_input


def test_coach_uses_persona_v3_as_single_source_of_truth() -> None:
    instructions = build_system_instructions()

    assert "# НеНой Persona v3 — Trainer" in instructions
    assert "Ной не ныл — и ты не ной" in instructions
    assert "ПОДЪЁБ → ПРАВДА → ХОД" in instructions
    assert "Базовый градус сарказма: 7/10" in instructions
    assert "Буллинг направляй только на отмазку" in instructions


def test_lightness_prompt_stays_independent() -> None:
    instructions = build_support_instructions()

    assert "# НеНойBot — режим поддержки" in instructions
    assert "Не требуй цель, отчёт, дедлайн или обязательное действие" in instructions
    assert "НеНой Persona v3" not in instructions
