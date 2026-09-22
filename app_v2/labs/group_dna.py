from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Mapping, Sequence


GROUP_DNA_VERSION = 1

_TIME_RE = re.compile(r"(?i)\b\d{1,2}(?::|\.)\d{2}\b|\bмск\b")
_EVENT_RE = re.compile(
    r"(?i)\b(?:занят\w*|тренинг\w*|встреч\w*|эфир\w*|"
    r"разбор\w*|практик\w*|конференц\w*)\b"
)
_DAY_RE = re.compile(
    r"(?i)\b(?:сегодня|завтра|понедельник\w*|вторник\w*|сред\w*|"
    r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b"
)
_REMINDER_RE = re.compile(r"(?i)\b(?:напомин\w*|не забуд\w*|жду всех|начинаем)\b")
_CHANGE_RE = re.compile(
    r"(?i)\b(?:перенос\w*|измен\w*|вместо|задерж\w*|пораньше|позже|"
    r"не будет|занятия нет|без изменений|всё без изменений|все без изменений)\b"
)
_ACCESS_RE = re.compile(
    r"(?i)(?:"
    r"где\s+(?:эта\s+|та\s+)?ссылк\w*|"
    r"нужн\w*\s+ссылк\w*|"
    r"как\s+.*(?:подключ|зайти|войти|вступить|найти)|"
    r"куда\s+.*(?:заход|вход|подключ)|"
    r"не\s+вижу\s+(?:ссылк\w*|кнопк\w*|видео|эфир\w*|встреч\w*|трансляц\w*|подключ\w*)|"
    r"(?:ссылк\w*|кнопк\w*|видео|эфир\w*|встреч\w*|трансляц\w*|подключ\w*).*не\s+вижу|"
    r"в\s+телеграм\w*\s+будет|"
    r"как\s+вас\s+там\s+найти"
    r")"
)
_MATERIAL_RE = re.compile(
    r"(?i)(?:"
    r"где\s+.*(?:запис|материал|текст|презентац|видео)|"
    r"где\s+находится\s+.*(?:текст|материал|запис)|"
    r"можно\s+.*(?:запис|материал|видео)|"
    r"ссылк\w*\s+на\s+.*(?:запис|материал|видео)|"
    r"репетиционн\w*\s+текст"
    r")"
)
_ATTENDANCE_RE = re.compile(
    r"(?i)\b(?:"
    r"не\s+смогу|не\s+успева\w*|опозда\w*|пропуска\w*|"
    r"присоединюсь|не\s+смог\w*\s+присоедин\w*|"
    r"посмотрю\s+(?:в\s+)?запис\w*|"
    r"буду\s+присутствовать|смогу\s+присутствовать"
    r")\b"
)
_SHORT_ATTENDANCE_RE = re.compile(
    r"(?i)^(?:(?:добрый\s+(?:день|вечер))[,!. ]*)?"
    r"(?:(?:я\s+)?(?:обязательно\s+)?буду(?:\s+обязательно)?)"
    r"[.! )🙏❤❤️😊🙂]*$"
)
_WELCOME_RE = re.compile(
    r"(?i)\b(?:приветству\w*|добро\s+пожаловать|рады\s+видеть|"
    r"рады\s+приветствовать)\b"
)
_FEEDBACK_RE = re.compile(
    r"(?i)\b(?:спасибо|благодарю|понравил\w*|супер\s+практик\w*|"
    r"прекрасн\w*\s+занят\w*|огненн\w*\s+встреч\w*)\b"
)

_JOIN_ACTIONS = {
    "join_group_by_link",
    "add_members",
    "invite_members",
    "join_group",
}

_CATEGORY_ORDER = (
    "admin_schedule_announcement",
    "admin_schedule_change",
    "access_or_link_question",
    "materials_or_recording_question",
    "attendance_or_availability",
    "newcomer_join",
    "admin_welcome",
    "gratitude_or_feedback",
)


def _text(item: Mapping[str, Any]) -> str:
    return " ".join(str(item.get("text") or "").split())


def _is_admin(item: Mapping[str, Any]) -> bool:
    return str(item.get("actor") or "") == "admin"


def _matches_category(item: Mapping[str, Any], category: str) -> bool:
    text = _text(item)
    kind = str(item.get("kind") or "message")
    is_admin = _is_admin(item)

    if category == "newcomer_join":
        return kind == "service" and str(item.get("service_action") or "") in _JOIN_ACTIONS

    if kind != "message" or not text:
        return False

    if category == "admin_schedule_announcement":
        return (
            is_admin
            and bool(_EVENT_RE.search(text))
            and bool(_TIME_RE.search(text) or _DAY_RE.search(text) or _REMINDER_RE.search(text))
        )

    if category == "admin_schedule_change":
        return (
            is_admin
            and bool(_EVENT_RE.search(text))
            and bool(_CHANGE_RE.search(text))
        )

    if category == "access_or_link_question":
        return (not is_admin) and bool(_ACCESS_RE.search(text))

    if category == "materials_or_recording_question":
        return (not is_admin) and bool(_MATERIAL_RE.search(text))

    if category == "attendance_or_availability":
        return (not is_admin) and bool(
            _ATTENDANCE_RE.search(text) or _SHORT_ATTENDANCE_RE.fullmatch(text)
        )

    if category == "admin_welcome":
        return is_admin and bool(_WELCOME_RE.search(text))

    if category == "gratitude_or_feedback":
        return (not is_admin) and bool(_FEEDBACK_RE.search(text))

    raise ValueError(f"unknown Group DNA category: {category}")


def _spread_sample(values: Sequence[str], limit: int) -> list[str]:
    if limit < 1:
        raise ValueError("sample limit must be >= 1")
    items = list(values)
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[len(items) // 2]]

    indexes = {
        round(position * (len(items) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [items[index] for index in sorted(indexes)]


def _candidate_roles(category_counts: Mapping[str, int]) -> list[dict[str, Any]]:
    definitions = (
        (
            "schedule_helper",
            ("admin_schedule_announcement", "admin_schedule_change"),
        ),
        ("access_helper", ("access_or_link_question",)),
        ("materials_curator", ("materials_or_recording_question",)),
        ("attendance_assistant", ("attendance_or_availability",)),
        ("newcomer_host", ("newcomer_join", "admin_welcome")),
    )

    result: list[dict[str, Any]] = []
    for role, categories in definitions:
        evidence = {name: int(category_counts.get(name, 0)) for name in categories}
        result.append(
            {
                "role": role,
                "evidence_count": sum(evidence.values()),
                "evidence_categories": evidence,
            }
        )
    return result


def analyze_group_dna(
    dataset: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    sample_per_category: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build deterministic operational Group DNA and a bounded review pack."""

    if sample_per_category < 1:
        raise ValueError("sample_per_category must be >= 1")

    raw_messages = dataset.get("messages")
    raw_cases = replay.get("cases")
    if not isinstance(raw_messages, list):
        raise ValueError("sanitized dataset must contain messages list")
    if not isinstance(raw_cases, list):
        raise ValueError("frozen replay must contain cases list")

    messages = [dict(item) for item in raw_messages if isinstance(item, Mapping)]
    cases = [dict(item) for item in raw_cases if isinstance(item, Mapping)]

    case_by_message_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        current = case.get("current")
        if not isinstance(current, Mapping):
            continue
        message_id = str(current.get("id") or "")
        if message_id:
            case_by_message_id[message_id] = case

    matched_ids: dict[str, list[str]] = OrderedDict((name, []) for name in _CATEGORY_ORDER)
    for item in messages:
        message_id = str(item.get("id") or "")
        if not message_id:
            continue
        for category in _CATEGORY_ORDER:
            if _matches_category(item, category):
                matched_ids[category].append(message_id)

    category_counts = {name: len(ids) for name, ids in matched_ids.items()}
    sampled_message_ids = {
        name: _spread_sample(ids, sample_per_category) if ids else []
        for name, ids in matched_ids.items()
    }

    selected_case_ids_by_category: dict[str, list[str]] = {}
    selected_cases: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for category in _CATEGORY_ORDER:
        case_ids: list[str] = []
        candidate_case_ids = [
            str(case_by_message_id[message_id].get("id") or "")
            for message_id in matched_ids[category]
            if message_id in case_by_message_id
        ]
        candidate_case_ids = [item for item in candidate_case_ids if item]
        for case_id in _spread_sample(candidate_case_ids, sample_per_category) if candidate_case_ids else []:
            if case_id not in case_ids:
                case_ids.append(case_id)
            case = next((item for item in cases if str(item.get("id") or "") == case_id), None)
            if case is not None:
                selected_cases.setdefault(case_id, case)
        selected_case_ids_by_category[category] = case_ids

    admin_messages = sum(
        1 for item in messages if str(item.get("kind") or "message") == "message" and _is_admin(item)
    )
    member_messages = sum(
        1
        for item in messages
        if str(item.get("kind") or "message") == "message" and not _is_admin(item)
    )
    service_messages = sum(1 for item in messages if str(item.get("kind") or "") == "service")

    stats = dataset.get("stats")
    stats_map = dict(stats) if isinstance(stats, Mapping) else {}

    dna = {
        "group_dna_version": GROUP_DNA_VERSION,
        "dataset_version": dataset.get("lab_dataset_version"),
        "source_replay_version": replay.get("lab_replay_version"),
        "scope": "operational_patterns_only",
        "privacy_note": "No psychological profiling; evidence references sanitized lab ids only.",
        "stats": {
            "messages_total": len(messages),
            "admin_messages": admin_messages,
            "member_messages": member_messages,
            "service_messages": service_messages,
            "first_timestamp": stats_map.get("first_timestamp"),
            "last_timestamp": stats_map.get("last_timestamp"),
        },
        "categories": {
            category: {
                "count": category_counts[category],
                "sample_message_ids": sampled_message_ids[category],
                "sample_case_ids": selected_case_ids_by_category[category],
            }
            for category in _CATEGORY_ORDER
        },
        "candidate_roles": _candidate_roles(category_counts),
    }

    review_pack = {
        "group_dna_review_pack_version": 1,
        "lab_replay_version": replay.get("lab_replay_version"),
        "dataset_version": dataset.get("lab_dataset_version"),
        "source_replay_version": replay.get("lab_replay_version"),
        "sample_per_category": sample_per_category,
        "category_case_ids": selected_case_ids_by_category,
        "stats": {
            "selected_unique_cases": len(selected_cases),
            "categories_with_cases": sum(
                1 for case_ids in selected_case_ids_by_category.values() if case_ids
            ),
        },
        "cases": list(selected_cases.values()),
    }

    return dna, review_pack
