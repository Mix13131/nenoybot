from __future__ import annotations

import json

import pytest

from app_v2.labs.group_history import (
    GroupLabOptions,
    ReplayOptions,
    build_frozen_replay,
    canonical_json_bytes,
    sanitize_telegram_export,
)


def _export():
    return {
        "name": "Sensitive Group Name",
        "type": "private_supergroup",
        "id": 1600927081,
        "messages": [
            {
                "id": 10,
                "type": "message",
                "date": "2022-02-07T14:16:39",
                "from": "Анна Тестова",
                "from_id": "user729337440",
                "text": [
                    "Встреча: ",
                    {"type": "link", "text": "https://example.com/join?secret=abc"},
                    "\nКод 510223, звоните +7 999 123-45-67",
                ],
                "reactions": [
                    {
                        "type": "emoji",
                        "count": 1,
                        "emoji": "👍",
                        "recent": [{"from": "Мария Участник", "from_id": "user2"}],
                    }
                ],
            },
            {
                "id": 11,
                "type": "message",
                "date": "2022-02-07T14:17:40",
                "from": "Мария Участник",
                "from_id": "user2",
                "reply_to_message_id": 10,
                "text": "Анна, мой email maria@example.org, поясните?",
            },
            {
                "id": 12,
                "type": "service",
                "date": "2022-02-07T14:18:00",
                "actor": "Мария Участник",
                "actor_id": "user2",
                "action": "join_group_by_link",
                "members": ["Иван Реальный"],
                "text": "",
            },
            {
                "id": 13,
                "type": "message",
                "date": "2022-02-07T17:00:00",
                "from": None,
                "from_id": "user3",
                "file": "(File not included.)",
                "file_name": "private-name.mp4",
                "media_type": "video_file",
                "mime_type": "video/mp4",
                "duration_seconds": 30,
                "text": "Смотрите @secret_user",
            },
        ],
    }


def test_sanitize_export_removes_raw_ids_names_and_sensitive_tokens():
    dataset = sanitize_telegram_export(
        _export(),
        options=GroupLabOptions(owner_display_name="Анна Тестова"),
    )

    serialized = canonical_json_bytes(dataset).decode("utf-8")
    assert "1600927081" not in serialized
    assert "user729337440" not in serialized
    assert "user2" not in serialized
    assert "Анна Тестова" not in serialized
    assert "Мария Участник" not in serialized
    assert "example.com" not in serialized
    assert "maria@example.org" not in serialized
    assert "+7 999 123-45-67" not in serialized
    assert "510223" not in serialized
    assert "@secret_user" not in serialized

    assert dataset["messages"][0]["actor"] == "admin"
    assert dataset["messages"][1]["actor"] == "member_001"
    assert "[link]" in dataset["messages"][0]["text"]
    assert "[number]" in dataset["messages"][0]["text"]
    assert "[phone]" in dataset["messages"][0]["text"]
    assert "[email]" in dataset["messages"][1]["text"]
    assert "[handle]" in dataset["messages"][3]["text"]


def test_reply_topology_and_reactions_survive_remapping_without_recent_actors():
    dataset = sanitize_telegram_export(
        _export(),
        options=GroupLabOptions(owner_source_id="user729337440"),
    )

    assert dataset["messages"][0]["id"] == "m000001"
    assert dataset["messages"][1]["id"] == "m000002"
    assert dataset["messages"][1]["reply_to"] == "m000001"
    assert dataset["messages"][0]["reactions"] == [{"emoji": "👍", "count": 1}]
    serialized = json.dumps(dataset["messages"][0]["reactions"], ensure_ascii=False)
    assert "Мария" not in serialized
    assert "user2" not in serialized


def test_media_is_safe_metadata_only():
    dataset = sanitize_telegram_export(_export())
    media = dataset["messages"][3]["media"]

    assert media["present"] is True
    assert media["media_type"] == "video_file"
    assert media["mime_type"] == "video/mp4"
    assert media["duration_seconds"] == 30
    serialized = json.dumps(media, ensure_ascii=False)
    assert "private-name.mp4" not in serialized
    assert "File not included" not in serialized


def test_same_input_and_options_are_byte_stable():
    options = GroupLabOptions(owner_display_name="Анна Тестова")
    first = sanitize_telegram_export(_export(), options=options)
    second = sanitize_telegram_export(_export(), options=options)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_frozen_replay_splits_on_gap_and_preserves_bounded_context():
    dataset = sanitize_telegram_export(
        _export(),
        options=GroupLabOptions(owner_display_name="Анна Тестова"),
    )
    replay = build_frozen_replay(
        dataset,
        options=ReplayOptions(
            inactivity_gap_minutes=60,
            max_episode_messages=10,
            context_messages=1,
        ),
    )

    assert replay["stats"]["episode_count"] == 2
    assert replay["stats"]["case_count"] == 3

    first_case, second_case, third_case = replay["cases"]
    assert first_case["context"] == []
    assert second_case["context"][0]["id"] == "m000001"
    assert second_case["current"]["reply_to"] == "m000001"
    assert third_case["episode_id"] != second_case["episode_id"]
    assert third_case["context"] == []
    assert all(case["review_label"] == "uncertain" for case in replay["cases"])
    assert all(case["expected_bot_text"] is None for case in replay["cases"])


def test_service_rows_can_be_excluded_without_leaking_member_names():
    dataset = sanitize_telegram_export(
        _export(),
        options=GroupLabOptions(include_service_messages=False),
    )

    assert all(item["kind"] != "service" for item in dataset["messages"])
    assert "Иван Реальный" not in canonical_json_bytes(dataset).decode("utf-8")



def test_short_display_name_does_not_corrupt_unrelated_words():
    payload = {
        "name": "Synthetic Group",
        "type": "private_supergroup",
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date": "2024-01-01T10:00:00",
                "from": "Ира",
                "from_id": "user1",
                "text": "Привет",
            },
            {
                "id": 2,
                "type": "message",
                "date": "2024-01-01T10:01:00",
                "from": "Саша",
                "from_id": "user2",
                "text": "Всем мира. Ира, привет.",
            },
        ],
    }

    dataset = sanitize_telegram_export(payload)
    text = dataset["messages"][1]["text"]

    assert "Всем мира." in text
    assert "member_001, привет." in text
    assert "мmember" not in text


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "tg://join?invite=SECRET",
        "telegram://join?invite=SECRET",
        "example.com/private/path?token=SECRET",
        "www.example.org/private",
        "https://example.net/private",
    ],
)
def test_common_url_forms_are_redacted(unsafe_text):
    payload = _export()
    payload["messages"][0]["text"] = unsafe_text

    dataset = sanitize_telegram_export(payload)

    assert dataset["messages"][0]["text"] == "[link]"
    assert "SECRET" not in canonical_json_bytes(dataset).decode("utf-8")


def test_source_ids_and_group_title_inside_text_are_redacted():
    payload = _export()
    payload["messages"][0]["text"] = (
        "Sensitive Group Name: user729337440 и channel9988 обсуждали встречу"
    )

    dataset = sanitize_telegram_export(payload)
    text = dataset["messages"][0]["text"]

    assert "Sensitive Group Name" not in text
    assert "user729337440" not in text
    assert "channel9988" not in text
    assert "[group]" in text
    assert text.count("[id]") == 2


def test_ambiguous_owner_name_fails_closed_but_source_id_can_disambiguate():
    payload = {
        "name": "Synthetic Group",
        "type": "private_supergroup",
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date": "2024-01-01T10:00:00",
                "from": "Анна",
                "from_id": "user1",
                "text": "one",
            },
            {
                "id": 2,
                "type": "message",
                "date": "2024-01-01T10:01:00",
                "from": "Анна",
                "from_id": "user2",
                "text": "two",
            },
        ],
    }

    with pytest.raises(ValueError, match="owner display name is ambiguous"):
        sanitize_telegram_export(
            payload,
            options=GroupLabOptions(owner_display_name="Анна"),
        )

    dataset = sanitize_telegram_export(
        payload,
        options=GroupLabOptions(owner_source_id="user2"),
    )
    assert dataset["messages"][0]["actor"] == "member_001"
    assert dataset["messages"][1]["actor"] == "admin"



@pytest.mark.parametrize(
    "payment_text",
    [
        "Реквизиты: 1111 2222 3333 4444 Получатель Synthetic Recipient",
        "Кто не вносил оплату за месяц, пожалуйста сюда:\n\n"
        "1111 2222 3333 4444\nSynthetic Recipient",
    ],
)
def test_payment_blocks_remove_identifiers_and_recipient_names(payment_text):
    payload = _export()
    payload["messages"][0]["text"] = payment_text

    dataset = sanitize_telegram_export(payload)
    text = dataset["messages"][0]["text"]
    serialized = canonical_json_bytes(dataset).decode("utf-8")

    assert "[payment]" in text
    assert "1111 2222" not in serialized
    assert "Synthetic Recipient" not in serialized


def test_payment_redaction_does_not_consume_unrelated_following_message_text():
    payload = _export()
    payload["messages"][0]["text"] = (
        "Оплата за месяц, реквизиты ниже:\n"
        "1111 2222 3333 4444\n"
        "Synthetic Recipient\n"
        "Завтра занятие в 18:30."
    )

    dataset = sanitize_telegram_export(payload)
    text = dataset["messages"][0]["text"]

    assert "Synthetic Recipient" not in text
    assert "Завтра занятие в 18:30." in text



def test_labeled_payment_recipient_is_redacted():
    payload = _export()
    numeric_marker = " ".join(["1111"] * 4)
    labeled_name = "Полу" + "чатель: Synthetic Recipient"
    payload["messages"][0]["text"] = (
        "Реквизиты для оплаты:\n" + numeric_marker + "\n" + labeled_name
    )

    dataset = sanitize_telegram_export(payload)
    serialized = canonical_json_bytes(dataset).decode("utf-8")

    assert "Synthetic Recipient" not in serialized


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "Мне понравился перевод этого стихотворения.",
        "Покажите карту города перед занятием.",
    ],
)
def test_non_payment_translation_and_map_language_is_preserved(ordinary_text):
    payload = _export()
    payload["messages"][0]["text"] = ordinary_text

    dataset = sanitize_telegram_export(payload)

    assert dataset["messages"][0]["text"] == ordinary_text
