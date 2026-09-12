from __future__ import annotations

# Guard only against clearly mechanical templates. Do not reject an otherwise
# natural reply because it contains ordinary words such as «понял» or «хорошо».
STYLE_BLOCKLIST: tuple[tuple[str, str], ...] = (
    ("срок поймал", "task-tracker tone"),
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
    "я не друг",
    "я не будильник",
    "я не ставлю реальные напоминания",
    "как ии",
    "как искусственный интеллект",
    "функция недоступна",
    "системные ограничения",
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
    """Return only unmistakably mechanical assistant phrases."""
    normalized = _normalize(text)
    return [phrase for phrase in BOTLIKE_PHRASES if phrase in normalized]


def is_human_style_response(text: str) -> bool:
    """True when the reply does not read like a service notification."""
    return not find_botlike_phrases(text)
