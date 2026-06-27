from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkBlock:
    id: str
    chat_id: int
    title: str
    domain: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    entities: tuple[str, ...] = field(default_factory=tuple)
    active: bool = True


@dataclass(frozen=True)
class WorkBlockMatch:
    block: WorkBlock | None
    score: int
    reason: str


DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "supply": (
        "китай",
        "корея",
        "поставщик",
        "производитель",
        "заказ",
        "фрез",
        "стикер",
        "циркон",
        "материалы",
        "позиции",
        "количество",
        "размеры",
        "тз",
        "прайс",
        "сроки поставки",
    ),
    "contracts": (
        "договор",
        "контракт",
        "аструм",
        "правки",
        "юрист",
        "согласование",
        "подписать",
        "почта",
        "проверить договор",
    ),
    "sales": (
        "клиент",
        "коммерческое",
        "кп",
        "счёт",
        "счет",
        "оплата",
        "лид",
        "продажа",
        "сделка",
    ),
}

ENTITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "supply": ("китай", "корея", "циркон", "фрез", "поставщик", "материалы"),
    "contracts": ("аструм", "договор", "контракт"),
    "sales": ("клиент", "кп", "счёт", "счет", "сделка"),
}

CONTINUATION_MARKERS = (
    "туда же",
    "по этому",
    "к этому",
    "ещё по",
    "еще по",
    "добавь к",
)

ENTITY_FORMS: dict[str, tuple[str, ...]] = {
    "китай": ("китай", "китае", "китая", "китаю", "китаем", "китайск"),
    "корея": ("корея", "корее", "кореи", "корею", "корейск"),
    "аструм": ("аструм", "аструмом", "аструму", "аструма"),
}


def infer_domain(text: str) -> str | None:
    normalized = _normalize(text)
    scores = {
        domain: sum(1 for keyword in keywords if keyword in normalized)
        for domain, keywords in DOMAIN_KEYWORDS.items()
    }
    domain, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        return None
    return domain


def suggest_block_title(text: str, domain: str | None) -> str:
    normalized = _normalize(text)

    if domain == "supply":
        has_china = _contains_entity(normalized, "китай")
        has_korea = _contains_entity(normalized, "корея")
        if has_china and has_korea:
            return "Поставки / Китай и Корея"
        if has_china:
            return "Поставки / Китай"
        if has_korea:
            return "Поставки / Корея"
        if "циркон" in normalized:
            return "Поставки / Циркон"
        return "Поставки"

    if domain == "contracts":
        if "аструм" in normalized:
            return "Договоры / Аструм"
        return "Договоры"

    if domain == "sales":
        return "Продажи"

    return "Рабочий блок"


def build_block_aliases(text: str, domain: str | None) -> tuple[str, ...]:
    if domain is None:
        return ()
    normalized = _normalize(text)
    return _unique(
        [
            *(
                keyword
                for keyword in DOMAIN_KEYWORDS.get(domain, ())
                if keyword in normalized
            ),
            *(
                canonical
                for canonical in ENTITY_FORMS
                if _contains_entity(normalized, canonical)
            ),
        ]
    )


def build_block_entities(text: str, domain: str | None) -> tuple[str, ...]:
    if domain is None:
        return ()
    normalized = _normalize(text)
    return _unique(
        (
            keyword
            for keyword in ENTITY_KEYWORDS.get(domain, ())
            if keyword in normalized or _contains_entity(normalized, keyword)
        )
    )


def match_work_block(text: str, blocks: list[WorkBlock]) -> WorkBlockMatch:
    normalized = _normalize(text)
    inferred_domain = infer_domain(text)
    has_continuation = any(marker in normalized for marker in CONTINUATION_MARKERS)

    best_block: WorkBlock | None = None
    best_score = 0
    best_reasons: list[str] = []

    for block in blocks:
        if not block.active:
            continue

        score = 0
        reasons: list[str] = []

        if any(alias and alias in normalized for alias in block.aliases):
            score += 3
            reasons.append("alias")

        if any(entity and entity in normalized for entity in block.entities):
            score += 4
            reasons.append("entity")

        if inferred_domain is not None and block.domain == inferred_domain:
            score += 2
            reasons.append("domain")

        if has_continuation:
            score += 3
            reasons.append("continuation")

        if score > best_score:
            best_block = block
            best_score = score
            best_reasons = reasons

    if best_block is None or best_score < 4:
        return WorkBlockMatch(block=None, score=best_score, reason="no match")

    return WorkBlockMatch(block=best_block, score=best_score, reason="+".join(best_reasons))


def create_work_block(chat_id: int, text: str) -> WorkBlock:
    domain = infer_domain(text) or "unknown"
    return WorkBlock(
        id=uuid.uuid4().hex,
        chat_id=chat_id,
        title=suggest_block_title(text, domain),
        domain=domain,
        aliases=build_block_aliases(text, domain),
        entities=build_block_entities(text, domain),
    )


def _normalize(text: str) -> str:
    return (text or "").casefold()


def _contains_entity(normalized_text: str, canonical: str) -> bool:
    return any(form in normalized_text for form in ENTITY_FORMS.get(canonical, (canonical,)))


def _unique(values) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
