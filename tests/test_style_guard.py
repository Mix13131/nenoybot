from app.style_guard import find_forbidden_style_phrases, is_style_guard_passed
from app.style_guard import find_botlike_phrases, is_human_style_response


def test_style_guard_rejects_bad_answers() -> None:
    bad_replies = [
        "Срок поймал до 17:00. По плану проверишь webhook.",
        "Принято, следующий шаг назначен.",
        "Главный срок без отчёта — это просто формальность.",
        "Главный удар не нужен, просто отчёт ожидаю.",
        "Заходи поздно: задача зафиксирована. Напоминание создано.",
        "Эмоции зафиксированы, ближайший шаг назначен.",
        "Приходи за фирменностью. Стиль не строит webhook.",
        "Срок без отчёта — это не оправдание.",
    ]

    for text in bad_replies:
        assert not is_style_guard_passed(text), text
        violations = find_forbidden_style_phrases(text)
        assert violations, text
        for violation in violations:
            assert "pattern" in violation and "reason" in violation


def test_style_guard_reports_reason() -> None:
    violations = find_forbidden_style_phrases("Срок поймал. По плану.")

    assert any(
        violation["pattern"] == "срок поймал"
        and violation["reason"] == "task-tracker tone"
        for violation in violations
    )
    assert any(
        violation["pattern"] == "по плану"
        and violation["reason"] == "CRM/task-tracker tone"
        for violation in violations
    )


def test_style_guard_allows_nenoy_style_examples() -> None:
    good_replies = [
        "Срок пришёл. Теперь нужен факт, а не красивая легенда про потом. Что сделал? 🔥",
        "Напоминалка сработала. Теперь пусть сработает дисциплина. Где результат? 💥",
        "План уже наговорили. До 17:00 запускаешь первый API-запрос и приносишь факт. ⛓️",
        "Ты пришёл не с отчётом по KPI, а с тратой оправданий. Что закрываешь первым?",
    ]

    for text in good_replies:
        assert is_style_guard_passed(text), text
        assert not find_forbidden_style_phrases(text), text


def test_style_guard_is_case_insensitive() -> None:
    violations = find_forbidden_style_phrases("ГлавНЫй УДАР и ОТЧЁТ ОЖИДАЮ")

    assert violations
    assert any(
        violation["pattern"] == "главный удар" and violation["reason"] == "artificial coach cliché"
        for violation in violations
    )
    assert any(
        violation["pattern"] == "отчёт ожидаю" and violation["reason"] == "manager tone"
        for violation in violations
    )


def test_find_botlike_phrases_is_case_insensitive() -> None:
    violations = find_botlike_phrases("Я НЕ БУДИЛЬНИК. Работай.")

    assert violations == ["я не будильник"]


def test_find_botlike_phrases_catches_empty_text() -> None:
    assert find_botlike_phrases("") == []


def test_find_botlike_phrases_catches_explicit_botness() -> None:
    violations = find_botlike_phrases("Усталость принята. Действуй дальше.")

    assert violations == ["усталость принята"]


def test_is_human_style_response() -> None:
    assert is_human_style_response("Устал? Бывает. Десять минут найдёшь. Открывай проект. 🔥")
    assert not is_human_style_response("Я не могу, это слишком сложно.")
