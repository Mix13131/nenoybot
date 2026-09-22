from __future__ import annotations

import json

import pytest

from app_v2.lab.telegram_importer import (
    TelegramExportImportConfig,
    TelegramExportImporter,
    write_dataset,
)


def payload():
    return {
        "name": "Private Community Name",
        "type": "private_supergroup",
        "id": 999999999,
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date": "2024-01-01T12:00:00",
                "date_unixtime": "1704110400",
                "from": "Alice Owner",
                "from_id": "user100",
                "text": "Добро пожаловать, Bob Member!",
                "text_entities": [
                    {"type": "plain", "text": "Добро пожаловать, Bob Member!"}
                ],
            },
            {
                "id": 2,
                "type": "message",
                "date": "2024-01-01T12:05:00",
                "date_unixtime": "1704110700",
                "edited": "2024-01-01T12:06:00",
                "edited_unixtime": "1704110760",
                "from": "Bob Member",
                "from_id": "user200",
                "reply_to_message_id": 1,
                "text": [
                    "Ссылка на встречу: ",
                    {
                        "type": "link",
                        "text": "https://example.zoom.us/j/12345678901?pwd=secret",
                    },
                    "\nКод доступа: 654321\nТелефон: +1 301 715 8592",
                ],
                "text_entities": [],
                "reactions": [
                    {
                        "type": "emoji",
                        "count": 2,
                        "emoji": "👍",
                        "recent": [
                            {
                                "from": "Alice Owner",
                                "from_id": "user100",
                                "date": "2024-01-01T12:06:00",
                            }
                        ],
                    }
                ],
            },
            {
                "id": 3,
                "type": "message",
                "date": "2024-01-01T12:10:00",
                "date_unixtime": "1704111000",
                "from": "Alice Owner",
                "from_id": "user100",
                "text": (
                    "Оплата, реквизиты: 4111 1111 1111 1111 Получатель Test Person\n"
                    "Публичный материал https://example.com/guide?utm_source=tg&chapter=1\n"
                    "Инвайт https://t.me/+AbCdEfGhIjKl"
                ),
                "text_entities": [],
            },
            {
                "id": 4,
                "type": "message",
                "date": "2024-01-01T12:15:00",
                "date_unixtime": "1704111300",
                "from": "Alice Owner",
                "from_id": "user100",
                "forwarded_from": "External Author",
                "forwarded_from_id": "channel555",
                "file": "(File not included.)",
                "file_name": "Private Lesson Name.mp4",
                "file_size": 123456,
                "media_type": "video_file",
                "mime_type": "video/mp4",
                "duration_seconds": 90,
                "width": 1280,
                "height": 720,
                "text": "Материал от Alice",
                "text_entities": [],
            },
            {
                "id": 5,
                "type": "service",
                "date": "2024-01-01T12:20:00",
                "date_unixtime": "1704111600",
                "actor": "Alice Owner",
                "actor_id": "user100",
                "action": "remove_members",
                "members": ["Bob Member", "Charlie Member"],
                "text": "",
                "text_entities": [],
            },
            {
                "id": 6,
                "type": "message",
                "date": "2024-01-01T12:25:00",
                "date_unixtime": "1704111900",
                "from": "Charlie Member",
                "from_id": "user300",
                "text": "Alice, а Bob уже заходил?",
                "text_entities": [],
            },
        ],
    }


def importer():
    return TelegramExportImporter(
        TelegramExportImportConfig(
            label="community_archive",
            owner_display_names=frozenset({"Alice Owner"}),
        )
    )


def test_importer_builds_canonical_dataset_without_source_chat_identity():
    dataset = importer().import_payload(payload())
    data = dataset.as_dict()
    dumped = json.dumps(data, ensure_ascii=False)

    assert data["schema_version"] == 1
    assert data["label"] == "community_archive"
    assert data["source_kind"] == "telegram_desktop_json"
    assert data["source_chat_type"] == "private_supergroup"
    assert len(data["events"]) == 6

    assert "Private Community Name" not in dumped
    assert "999999999" not in dumped
    assert "user100" not in dumped
    assert "user200" not in dumped
    assert "user300" not in dumped


def test_owner_and_participants_get_stable_pseudonyms_and_names_do_not_leak():
    dataset = importer().import_payload(payload())

    first = dataset.events[0]
    second = dataset.events[1]
    sixth = dataset.events[5]
    dumped = json.dumps(dataset.as_dict(), ensure_ascii=False)

    assert first.actor_alias == "owner_001"
    assert first.actor_role == "owner"
    assert second.actor_alias == "user_001"
    assert sixth.actor_alias == "user_002"

    for raw_name in (
        "Alice Owner",
        "Bob Member",
        "Charlie Member",
        "Alice",
        "Bob",
        "Charlie",
    ):
        assert raw_name.casefold() not in dumped.casefold()


def test_mixed_text_reply_edit_reactions_are_preserved_safely():
    dataset = importer().import_payload(payload())
    event = dataset.events[1]

    assert event.reply_to_source_message_id == 1
    assert event.edited_at is not None
    assert event.reactions[0].emoji == "👍"
    assert event.reactions[0].count == 2
    assert "[REDACTED_MEETING_LINK]" in event.text
    assert "[REDACTED_ACCESS_CODE]" in event.text
    assert "[REDACTED_PHONE]" in event.text
    assert "secret" not in event.text


def test_payment_and_invite_credentials_are_removed_but_public_resource_remains():
    dataset = importer().import_payload(payload())
    event = dataset.events[2]

    assert "[REDACTED_PAYMENT_DETAILS]" in event.text
    assert "4111" not in event.text
    assert "[REDACTED_INVITE_LINK]" in event.text

    assert "https://example.com/guide?chapter=1" in event.text
    assert "utm_source" not in event.text


def test_media_keeps_safe_metadata_but_drops_original_filename():
    dataset = importer().import_payload(payload())
    event = dataset.events[3]
    dumped = json.dumps(event.as_dict(), ensure_ascii=False)

    assert event.media is not None
    assert event.media.media_type == "video_file"
    assert event.media.mime_type == "video/mp4"
    assert event.media.file_extension == ".mp4"
    assert event.media.file_size == 123456
    assert event.media.duration_seconds == 90
    assert event.forwarded_actor_alias == "channel_001"

    assert "Private Lesson Name" not in dumped
    assert "External Author" not in dumped


def test_service_event_keeps_action_and_member_count_not_member_names():
    dataset = importer().import_payload(payload())
    event = dataset.events[4]
    dumped = json.dumps(event.as_dict(), ensure_ascii=False)

    assert event.event_type == "service"
    assert event.service_action == "remove_members"
    assert event.actor_alias == "owner_001"
    assert event.metadata["member_count"] == 2
    assert "Bob Member" not in dumped
    assert "Charlie Member" not in dumped


def test_dataset_id_and_aliases_are_deterministic_for_same_export():
    first = importer().import_payload(payload())
    second = importer().import_payload(payload())

    assert first.dataset_id == second.dataset_id
    assert [event.actor_alias for event in first.events] == [
        event.actor_alias for event in second.events
    ]


def test_redaction_stats_are_reported_without_raw_values():
    dataset = importer().import_payload(payload())
    stats = dataset.stats

    assert stats["event_count"] == 6
    assert stats["message_count"] == 5
    assert stats["service_count"] == 1
    assert stats["reaction_count"] == 2
    assert stats["redactions"]["credential_url"] >= 2
    assert stats["redactions"]["access_code"] >= 1
    assert stats["redactions"]["phone"] >= 1
    assert stats["redactions"]["payment_details"] >= 1
    assert stats["redactions"]["person_name"] >= 1


def test_writer_outputs_only_canonical_dataset(tmp_path):
    dataset = importer().import_payload(payload())
    output = tmp_path / "safe.json"

    write_dataset(dataset, output)

    raw = output.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["dataset_id"] == dataset.dataset_id
    assert "user100" not in raw
    assert "Private Community Name" not in raw
    assert "4111 1111 1111 1111" not in raw


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        {},
        {"messages": "not-a-list"},
    ],
)
def test_invalid_export_shape_fails_closed(invalid):
    with pytest.raises(ValueError):
        importer().import_payload(invalid)



def test_member_name_seen_before_source_id_reuses_same_alias():
    data = payload()
    data["messages"].insert(
        0,
        {
            "id": 0,
            "type": "service",
            "date": "2024-01-01T11:59:00",
            "date_unixtime": "1704110340",
            "actor": "Private Community Name",
            "actor_id": "channel999",
            "action": "add_members",
            "members": ["Alice Owner", "Bob Member"],
            "text": "",
            "text_entities": [],
        },
    )

    dataset = importer().import_payload(data)
    bob_message = next(
        event for event in dataset.events if event.source_message_id == 2
    )

    assert bob_message.actor_alias == "user_001"
    assert dataset.stats["participant_count"] == 5


@pytest.mark.parametrize("tracking_key", ["fbclid", "gclid", "yclid", "msclkid", "ttclid"])
def test_common_tracking_query_identifiers_are_removed(tracking_key):
    data = payload()
    data["messages"][0]["text"] = (
        f"https://example.com/resource?chapter=1&{tracking_key}=participant-token"
    )

    dataset = importer().import_payload(data)
    text = dataset.events[0].text

    assert "participant-token" not in text
    assert tracking_key not in text
    assert "chapter=1" in text


@pytest.mark.parametrize(
    "url,marker",
    [
        ("https://t.me:443/+AbCdEfGhIjKl", "[REDACTED_INVITE_LINK]"),
        ("https://vk.com:443/call/join/secret-path", "[REDACTED_CALL_LINK]"),
        (
            "https://example.zoom.us:443/j/123456789?pwd=secret",
            "[REDACTED_MEETING_LINK]",
        ),
    ],
)
def test_sensitive_links_with_explicit_ports_are_redacted(url, marker):
    data = payload()
    data["messages"][0]["text"] = url

    dataset = importer().import_payload(data)

    assert dataset.events[0].text == marker
    assert "secret" not in dataset.events[0].text


def test_root_chat_title_is_redacted_even_without_matching_actor():
    data = payload()
    data["name"] = "Never Seen Root Title"
    data["messages"][0]["text"] = "Обсуждаем Never Seen Root Title сегодня"

    dataset = importer().import_payload(data)
    dumped = json.dumps(dataset.as_dict(), ensure_ascii=False)

    assert "Never Seen Root Title" not in dumped
    assert "[SOURCE_CHAT]" in dataset.events[0].text


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "Клуб БРИЛЛИАНТОВЫЙ ГОЛОС",
        "Anna Terekhova",
        "anna/diamond",
        "anna@example.com",
        "anna_diamond_voice",
        "alice_owner",
        "private_community_name",
    ],
)
def test_dataset_label_requires_neutral_dataset_class(unsafe_label):
    with pytest.raises(ValueError, match="neutral dataset class"):
        TelegramExportImporter(
            TelegramExportImportConfig(label=unsafe_label)
        )


@pytest.mark.parametrize(
    "safe_label",
    [
        "community_archive",
        "friends_archive",
        "work_archive",
        "channel_archive",
        "generic_archive",
    ],
)
def test_neutral_dataset_classes_are_allowed(safe_label):
    instance = TelegramExportImporter(
        TelegramExportImportConfig(label=safe_label)
    )
    assert instance.config.label == safe_label



def test_distinct_source_ids_with_same_display_name_remain_distinct():
    data = {
        "name": "Synthetic Group",
        "type": "private_supergroup",
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date_unixtime": "1704110400",
                "from": "Alex Smith",
                "from_id": "user1",
                "text": "первый",
            },
            {
                "id": 2,
                "type": "message",
                "date_unixtime": "1704110460",
                "from": "Alex Smith",
                "from_id": "user2",
                "text": "второй",
            },
            {
                "id": 3,
                "type": "message",
                "date_unixtime": "1704110520",
                "from": "Other Person",
                "from_id": "user3",
                "text": "Alex Smith, вы оба здесь?",
            },
        ],
    }
    dataset = TelegramExportImporter(
        TelegramExportImportConfig(label="community_archive")
    ).import_payload(data)

    assert dataset.events[0].actor_alias == "user_001"
    assert dataset.events[1].actor_alias == "user_002"
    assert dataset.events[0].actor_alias != dataset.events[1].actor_alias
    assert "[PERSON]" in dataset.events[2].text
    assert dataset.stats["participant_count"] == 3


def test_ambiguous_owner_display_name_fails_closed():
    data = {
        "name": "Synthetic Group",
        "type": "private_supergroup",
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date_unixtime": "1704110400",
                "from": "Alex Smith",
                "from_id": "user1",
                "text": "one",
            },
            {
                "id": 2,
                "type": "message",
                "date_unixtime": "1704110460",
                "from": "Alex Smith",
                "from_id": "user2",
                "text": "two",
            },
        ],
    }
    instance = TelegramExportImporter(
        TelegramExportImportConfig(
            label="community_archive",
            owner_display_names=frozenset({"Alex Smith"}),
        )
    )

    with pytest.raises(ValueError, match="owner display name is ambiguous"):
        instance.import_payload(data)


@pytest.mark.parametrize(
    "tracking_key",
    ["wbraid", "gbraid", "srsltid", "li_fat_id", "_gl", "random_click_id"],
)
def test_unknown_and_additional_tracking_params_are_dropped(tracking_key):
    data = payload()
    data["messages"][0]["text"] = (
        f"https://example.com/resource?chapter=1&{tracking_key}=unique-value"
    )

    dataset = importer().import_payload(data)
    value = dataset.events[0].text

    assert "unique-value" not in value
    assert tracking_key not in value
    assert "chapter=1" in value
