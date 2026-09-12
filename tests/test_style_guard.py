from app.style_guard import find_forbidden_style_phrases, is_style_guard_passed
from app.style_guard import find_botlike_phrases, is_human_style_response


def test_style_guard_rejects_clearly_mechanical_answers() -> None:
    bad_replies = [
        "Срок поймал до 17:00. Отчёт ожидаю.",
        "Задача зафиксирована. Напоминание создано.",
        "Эмоции зафиксированы, ближайший шаг назначен.",
        "Приходи за фирменностью. Стиль не строит webhook.",
        "Главный удар не нужен, просто отчёт ожидаю.",
        "Срок без отчёта — это не оправдание.",
    ]

    for text in bad_replies:
        assert not is_style_guard_passed(text), text
        violations = find_forbidden_style_phrases(text)
        assert violations, text
        for violation in violations:
            assert "pattern" in violation and "reason" in violation


def test_style_guard_reports_reason() -> None:
    violations = find_forbidden_style_phrases("Срок поймал. Отчёт ожидаю.")

    assert any(
        violation["pattern"] == "срок поймал"
        and violation["reason"] == "task-tracker tone"
        for violation in violations
    )
    assert any(
        violation["pattern"] == "отчёт ожидаю"
        and violation["reason"] == "manager tone"
        for violation in violations
    )


def test_style_guard_allows_natural_everyday_words() -> None:
    replies = [
        "Понял. Тут проект пока вообще не трогаем — расскажи, что именно бесит.",
        "Хорошо, это уже звучит как нормальный разговор 😏",
        "Принято. Тогда сегодня без геройства.",
        "По плану всё ок, но сейчас ты явно хочешь просто выдохнуть.",
    ]

    for text in replies:
        assert is_style_guard_passed(text), text
        assert is_human_style_response(text), text


def test_style_guard_allows_nenoy_style_examples() -> None:
    good_replies = [
        "Диван опять баллотируется в президенты твоего дня. Но сегодня у него сильная кампания 😏",
        "Красоту наведёшь потом. Сейчас нужен грязный черновик.",
        "О, это уже маленький развод с проектом. Ты его реально больше не хочешь — или сегодня видеть не можешь?",
        "Факт есть 🔥 Теперь можно секунду порадоваться и решить, нужен ли следующий кусок прямо сейчас.",
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
    assert is_human_style_response("Устал? Тогда без цирка. Хочешь выдохнуть — выдыхай.")
    assert not is_human_style_response("Системные ограничения не позволяют это сделать.")
