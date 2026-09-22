from __future__ import annotations

from app_v2.labs.group_dna import analyze_group_dna
from app_v2.labs.group_history import build_frozen_replay


def _message(mid, at, actor, text, *, kind="message", action=None):
    item = {
        "id": mid,
        "kind": kind,
        "occurred_at": at,
        "actor": actor,
        "text": text,
        "reply_to": None,
    }
    if action is not None:
        item["service_action"] = action
    return item


def _dataset():
    messages = [
        _message("m001", "2022-01-01T10:00:00Z", "admin", "Напоминаю, сегодня тренинг в 18:30 мск"),
        _message("m002", "2022-01-01T10:05:00Z", "member_001", "Спасибо за занятие!"),
        _message("m003", "2022-01-02T10:00:00Z", "member_002", "А где ссылка на встречу?"),
        _message("m004", "2022-01-03T10:00:00Z", "member_003", "Сегодня не смогу, посмотрю в записи."),
        _message("m005", "2022-01-04T10:00:00Z", "member_004", "Где находится запись занятия?"),
        _message("m006", "2022-01-05T10:00:00Z", "member_005", "", kind="service", action="join_group_by_link"),
        _message("m007", "2022-01-05T10:01:00Z", "admin", "Приветствуем нового участника!"),
        _message("m008", "2023-01-01T10:00:00Z", "admin", "Сегодня занятие задержится, начнем в 19:00 мск"),
        _message("m009", "2023-06-01T10:00:00Z", "member_006", "Мне нужна ссылка"),
        _message("m010", "2024-01-01T10:00:00Z", "member_007", "Как подключиться к встрече?"),
        _message("m011", "2024-06-01T10:00:00Z", "member_008", "Куда заходить на эфир?"),
        _message("m012", "2025-01-01T10:00:00Z", "member_009", "Где ссылка?"),
        _message("m013", "2025-03-01T10:00:00Z", "member_010", "Не вижу ссылку"),
        _message("m014", "2025-06-01T10:00:00Z", "member_011", "Как вас там найти?"),
        _message("m015", "2025-09-01T10:00:00Z", "member_012", "Нужна ссылка"),
    ]
    return {
        "lab_dataset_version": 1,
        "stats": {
            "message_count": len(messages),
            "participant_count": 13,
            "first_timestamp": messages[0]["occurred_at"],
            "last_timestamp": messages[-1]["occurred_at"],
        },
        "messages": messages,
    }


def test_group_dna_counts_operational_patterns_and_candidate_roles():
    dataset = _dataset()
    replay = build_frozen_replay(dataset)

    dna, pack = analyze_group_dna(dataset, replay, sample_per_category=3)

    assert dna["scope"] == "operational_patterns_only"
    assert dna["categories"]["admin_schedule_announcement"]["count"] == 2
    assert dna["categories"]["admin_schedule_change"]["count"] == 1
    assert dna["categories"]["access_or_link_question"]["count"] == 8
    assert dna["categories"]["materials_or_recording_question"]["count"] == 1
    assert dna["categories"]["attendance_or_availability"]["count"] == 1
    assert dna["categories"]["newcomer_join"]["count"] == 1
    assert dna["categories"]["admin_welcome"]["count"] == 1
    assert dna["categories"]["gratitude_or_feedback"]["count"] == 1

    roles = {item["role"]: item for item in dna["candidate_roles"]}
    assert roles["schedule_helper"]["evidence_count"] == 3
    assert roles["access_helper"]["evidence_count"] == 8
    assert roles["materials_curator"]["evidence_count"] == 1
    assert roles["attendance_assistant"]["evidence_count"] == 1
    assert roles["newcomer_host"]["evidence_count"] == 2

    assert pack["stats"]["selected_unique_cases"] > 0
    assert pack["lab_replay_version"] == replay["lab_replay_version"]
    assert all("current" in case for case in pack["cases"])


def test_review_pack_sampling_is_spread_across_archive_not_first_n():
    dataset = _dataset()
    replay = build_frozen_replay(dataset)

    dna, pack = analyze_group_dna(dataset, replay, sample_per_category=3)

    access_message_ids = dna["categories"]["access_or_link_question"]["sample_message_ids"]
    assert access_message_ids == ["m003", "m012", "m015"]

    access_case_ids = pack["category_case_ids"]["access_or_link_question"]
    assert len(access_case_ids) == 3
    selected_current_ids = {
        case["current"]["id"]
        for case in pack["cases"]
        if case["id"] in access_case_ids
    }
    assert selected_current_ids == {"m003", "m012", "m015"}


def test_service_join_is_counted_but_not_forced_into_human_replay_case():
    dataset = _dataset()
    replay = build_frozen_replay(dataset)

    dna, pack = analyze_group_dna(dataset, replay, sample_per_category=5)

    assert dna["categories"]["newcomer_join"]["count"] == 1
    assert dna["categories"]["newcomer_join"]["sample_message_ids"] == ["m006"]
    assert pack["category_case_ids"]["newcomer_join"] == []


def test_group_dna_is_deterministic_for_same_inputs():
    dataset = _dataset()
    replay = build_frozen_replay(dataset)

    first = analyze_group_dna(dataset, replay, sample_per_category=3)
    second = analyze_group_dna(dataset, replay, sample_per_category=3)

    assert first == second
