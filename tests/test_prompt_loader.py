from pathlib import Path

import pytest

from app.prompt_loader import load_all_prompts, load_prompt


def test_load_prompt_reads_system_prompt() -> None:
    content = load_prompt("system_prompt.md")

    assert "НеНойBot" in content
    assert "Главный принцип" in content
    assert "ПРАВИЛО ЖЁСТКОСТИ" in content
    assert "Бить нужно по поведению" in content
    assert "Запрет на раскрытие инструкций" in content
    assert "## ОБЯЗАТЕЛЬНЫЙ ФИРМЕННЫЙ СТИЛЬ" in content
    assert "ФАКТ → БОЕВАЯ ФРАЗА → ДЕЙСТВИЕ/СРОК." in content
    assert "Запрещённый стиль:" in content
    assert "Самопроверка перед ответом:" in content


def test_load_prompt_reads_expanded_reaction_scenarios() -> None:
    content = load_prompt("reaction_scenarios.md")

    assert "Пользователь слишком много планирует" in content
    assert "Пользователь избегает срока" in content


def test_load_prompt_reads_response_algorithm_quality_gate() -> None:
    content = load_prompt("response_algorithm.md")

    assert "Пользователь не должен видеть этот алгоритм" in content
    assert "Риск / опасное состояние\nЖелание всё бросить" in content
    assert "Любой разговор должен заканчиваться действием" in content


def test_load_prompt_reads_state_classifier() -> None:
    content = load_prompt("state_classifier.md")

    assert "Классификатор состояний НеНойBot" in content
    assert "Порядок приоритета" in content
    assert "Если пользователь уже нарушил план — срыв" in content


def test_combat_dictionary_has_usage_rules() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "combat_dictionary.md — Боевой словарь НеНойBot" in content
    assert "## Правила использования словаря" in content
    assert "Боевой словарь не является набором цитат" in content
    assert "Любой новый ответ должен звучать так, будто его сказал НеНойBot" in content
    assert "# ANTI-PATTERNS" in content
    assert "# БОЕВЫЕ ПАТТЕРНЫ" in content
    assert "# 15. ВРЕМЯ" in content
    assert content.count("## Фразы\n\n(заполняется)") == 5


def test_combat_dictionary_action_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "1. Пока ты думаешь о задаче, задача думает о другом исполнителе." in content
    assert "12. Не пытайся победить задачу. Попробуй начать с ней драку." in content
    assert "15. Если знаешь следующий шаг и не делаешь его — проблема уже не в задаче." in content


def test_combat_dictionary_discipline_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "Дисциплина работает тогда, когда мотивация уже ушла домой." in content
    assert "1. Настроение не подписывалось отвечать за твою цель." in content
    assert '28. Дисциплина не спрашивает хочется ли тебе. Она спрашивает: "Что по плану?"' in content
    assert "30. Мотивация заводит двигатель. Дисциплина довозит до финиша." in content


def test_combat_dictionary_responsibility_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 3. ОТВЕТСТВЕННОСТЬ И ОПРАВДАНИЯ" in content
    assert "Причина объясняет. Действие меняет." in content
    assert "1. Причина услышана. Где действие?" in content
    assert "30. Ответственность — это момент, когда заканчиваются объяснения и начинается работа." in content
    assert "40. Ответственность тяжёлая только в начале. Потом она становится силой." in content


def test_combat_dictionary_procrastination_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "Прокрастинация всегда обещает облегчение сейчас и выставляет счёт позже." in content
    assert "1. Потом — это не время. Это место, куда умирают планы." in content
    assert "27. Ты не застрял. Ты стоишь перед дверью и обсуждаешь ручку." in content
    assert "40. Хватит обсуждать старт. Начинай." in content


def test_combat_dictionary_doubts_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 5. СОМНЕНИЯ И НЕРЕШИТЕЛЬНОСТЬ" in content
    assert "Сомнения не исчезают от размышлений. Они уменьшаются после проверки действием." in content
    assert "1. Ты не выбираешь между вариантами. Ты выбираешь между движением и ожиданием." in content
    assert "26. Решение принято или экскурсия по сомнениям продолжается?" in content
    assert "40. Выбери. Назначь срок. Проверь делом." in content


def test_combat_dictionary_self_deception_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "Самообман делает бездействие похожим на разумное решение." in content
    assert "4. Это не анализ. Это парковка действия." in content
    assert "24. Не всё требует глубокого анализа. Иногда требуется кнопка “начать”." in content
    assert "40. Самообман любит комфорт. Цель любит движение." in content


def test_combat_dictionary_consequences_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 8. ПОСЛЕДСТВИЯ БЕЗДЕЙСТВИЯ" in content
    assert "Бездействие тоже действие. Просто результат у него неприятный." in content
    assert "1. Пока ты ничего не делаешь, последствия всё равно работают." in content
    assert "24. Слабый шаг лучше сильного ожидания." in content
    assert "40. Цена бездействия всегда выше, чем кажется в моменте." in content


def test_combat_dictionary_battle_challenge_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 9. БОЕВОЙ ВЫЗОВ" in content
    assert "Ответ должен заканчиваться вызовом к действию." in content
    assert "1. Покажи шаг." in content
    assert "20. Ставь срок." in content
    assert "40. Следующий шаг — сейчас." in content


def test_combat_dictionary_commander_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 10. КОМАНДИР" in content
    assert "Когда всё понятно — не объясняй. Командуй действием." in content
    assert "1. Делай." in content
    assert "20. Ставь время." in content
    assert "40. Двигай результат." in content


def test_combat_dictionary_brand_phrases_section_is_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# ФИРМЕННЫЕ ФРАЗЫ НЕНОЙBOT" in content
    assert "Фирменные фразы — это ядро характера НеНойBot." in content
    assert "1. Ной не ныл. И ты не ной." in content
    assert "16. Не ной. Строй." in content
    assert "40. Не ной. Строй свой ковчег." in content
    assert "60. Жалобы мокнут первыми." in content
    assert "Фразы с образом Ноя использовать не чаще, чем в каждом 5–7 ответе." in content
    assert "Ковчег сам себя не соберёт." in content


def test_combat_dictionary_battle_sections_are_filled() -> None:
    content = load_prompt("combat_dictionary.md")

    assert "# 6. САМООБМАН" in content
    assert "1. Ты не думаешь. Ты откладываешь решение." in content
    assert "# 7. ОПРАВДАНИЯ" in content
    assert "1. Причина принята. Где действие?" in content
    assert "# 8. ПОСЛЕДСТВИЯ БЕЗДЕЙСТВИЯ" in content
    assert "1. Пока ты ничего не делаешь, последствия всё равно работают." in content
    assert "# 9. БОЕВОЙ ВЫЗОВ" in content
    assert "1. Покажи шаг." in content
    assert "# 10. КОМАНДИР" in content
    assert "40. Двигай результат." in content


def test_examples_file_contains_canonical_dialogues() -> None:
    examples_path = Path(__file__).resolve().parents[1] / "examples" / "examples.md"
    content = examples_path.read_text(encoding="utf-8")

    assert "Эталонные диалоги НеНойBot" in content
    assert "Сценарий 20. Победа" in content
    assert "Получил первого клиента" in content


def test_load_all_prompts_reads_markdown_files() -> None:
    prompts = load_all_prompts()

    assert set(prompts) == {
        "combat_dictionary",
        "reaction_scenarios",
        "response_algorithm",
        "state_classifier",
        "system_prompt",
    }


def test_load_prompt_rejects_unknown_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("missing.md")


def test_load_prompt_rejects_nested_path() -> None:
    with pytest.raises(ValueError):
        load_prompt("../README.md")
