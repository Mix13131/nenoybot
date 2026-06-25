from __future__ import annotations

STYLE_BLOCKLIST: tuple[tuple[str, str], ...] = (
    ("по плану", "CRM/task-tracker tone"),
    ("срок поймал", "task-tracker tone"),
    ("принято", "soft assistant tone"),
    ("главный срок", "manager tone"),
    ("главный удар", "artificial coach cliché"),
    ("отчёт ожидаю", "manager tone"),
    ("задача зафиксирована", "CRM/task-tracker tone"),
    ("срок без отчёта", "manager tone"),
    ("доски ждут", "weak meta-style phrase"),
    ("напоминание создано", "system notification tone"),
    ("эмоции зафиксированы", "psychologist/CRM tone"),
    ("ближайший шаг назначен", "task-tracker tone"),
    ("приходи за фирменностью", "weak meta-style phrase"),
    ("стиль не строит", "weak meta-style phrase"),
)

BOTLIKE_PHRASES: tuple[str, ...] = (
    "усталость принята",
    "запрос принят",
    "принято",
    "понял",
    "хорошо",
    "выполнено",
    "я не друг",
    "я не будильник",
    "я не ставлю реальные напоминания",
    "я не могу",
    "я не умею",
    "как ии",
    "как искусственный интеллект",
    "функция недоступна",
    "системные ограничения",
    "технически невозможно",
)


def _normalize(text: str) -> str:
    return (text or "").casefold()


def find_forbidden_style_phrases(text: str) -> list[dict[str, str]]:
    normalized = _normalize(text)
    violations: list[dict[str, str]] = []
    for pattern, reason in STYLE_BLOCKLIST:
        if pattern in normalized:
            violations.append({"pattern": pattern, "reason": reason})
    return violations


def is_style_guard_passed(text: str) -> bool:
    return not find_forbidden_style_phrases(text)


def find_botlike_phrases(text: str) -> list[str]:
    """Возвращает список роботских фраз, найденных в тексте."""
    normalized = _normalize(text)
    return [phrase for phrase in BOTLIKE_PHRASES if phrase in normalized]


def is_human_style_response(text: str) -> bool:
    """True, если ответ не звучит как служебный/ботовский автоответ."""
    return not find_botlike_phrases(text)
