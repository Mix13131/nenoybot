"""Synthetic Desktop snapshots: provenance, identity, loss and topic evidence."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app_v2.adapters import telegram_history
from app_v2.adapters.telegram_history import HistoryExportError, parse_telegram_desktop_export


def topic(identifier=31):
    return {"id": identifier, "type": "service", "action": "topic_created",
            "date_unixtime": "1790000000", "title": "Synthetic topic"}


def message(identifier=32, **changes):
    result = {
        "id": identifier, "type": "message", "from": "Synthetic author",
        "from_id": "user7001", "date": "untrusted locale date",
        "date_unixtime": "1790000001", "reply_to_message_id": 31,
        "text": "  Синтетический отчёт\nс исходными пробелами.  ",
    }
    result.update(changes)
    return result


def export(*items, **changes):
    result = {"name": "Synthetic group", "id": 7000, "type": "public_supergroup",
              "messages": list(items) or [topic(), message()]}
    result.update(changes)
    return result


def project(**changes):
    return parse_telegram_desktop_export(export(topic(), message(**changes))).messages[0]


def test_snapshot_preserves_identities_whitespace_and_authoritative_utc_date():
    parsed = parse_telegram_desktop_export(export(), expected_chat_id=-1000000007000)
    item = parsed.messages[0]
    assert parsed.input_messages == 2
    assert parsed.skipped_counts == {"service": 1}
    assert parsed.warning_counts == {}
    assert parsed.duplicate_messages == 0
    assert item.chat_id == -1000000007000
    assert item.message_thread_id == 31
    assert item.telegram_message_id == 32
    assert item.author_user_id == 7001
    assert item.reply_to_message_id == 31
    assert item.available_text == message()["text"]
    assert item.body_coverage == "full"
    assert item.occurred_at == datetime.fromtimestamp(1790000001, timezone.utc)
    assert item.revision_at == item.occurred_at
    assert item.source_kind == "telegram_desktop_export"
    assert not hasattr(item, "telegram_update_id")
    assert not hasattr(item, "source_id")


def test_edited_snapshot_does_not_invent_intermediate_versions():
    item = project(edited="locale-only", edited_unixtime="1790000010")
    assert item.occurred_at == datetime.fromtimestamp(1790000001, timezone.utc)
    assert item.revision_at == datetime.fromtimestamp(1790000010, timezone.utc)
    assert item.is_edited


def test_topic_membership_follows_exported_reply_evidence_across_shuffled_records():
    data = export(message(35, reply_to_message_id=34, date_unixtime="1790000004"),
                  topic(41), message(34, reply_to_message_id=32, date_unixtime="1790000003"),
                  message(42, reply_to_message_id=41, date_unixtime="1790000002"),
                  topic(), message())
    items = parse_telegram_desktop_export(data).messages
    assert [(m.telegram_message_id, m.message_thread_id) for m in items] == [
        (32, 31), (42, 41), (34, 31), (35, 31),
    ]


def test_unknown_supergroup_topic_is_counted_not_guessed_from_numbers_or_text():
    data = export(message(reply_to_message_id=9999, text="Topic 31, General"))
    parsed = parse_telegram_desktop_export(data)
    assert parsed.messages == ()
    assert parsed.skipped_counts == {"unresolved_topic": 1}


def test_explicit_operator_confirmation_can_resolve_otherwise_unknown_topic():
    parsed = parse_telegram_desktop_export(export(message(reply_to_message_id=9999)),
                                           confirmed_thread_id=31)
    assert parsed.messages[0].message_thread_id == 31


def test_explicit_thread_does_not_depend_on_exported_service_event():
    parsed = parse_telegram_desktop_export(export(message(message_thread_id=41)))
    assert parsed.messages[0].message_thread_id == 41


def test_ordinary_private_group_has_canonical_root_and_correct_group_identity():
    data = export(message(reply_to_message_id=9999), type="private_group")
    parsed = parse_telegram_desktop_export(data)
    assert parsed.chat_id == -7000
    assert parsed.messages[0].message_thread_id == 1


def test_ordinary_group_cannot_claim_forum_topic_metadata():
    with pytest.raises(HistoryExportError, match="ordinary group"):
        parse_telegram_desktop_export(export(type="private_group"))
    with pytest.raises(HistoryExportError, match="ordinary group"):
        parse_telegram_desktop_export(export(message(message_thread_id=41), type="private_group"))
    with pytest.raises(HistoryExportError, match="ordinary group"):
        parse_telegram_desktop_export(export(message(), type="private_group"), confirmed_thread_id=41)


@pytest.mark.parametrize("items", [
    [topic(), message(message_thread_id=31, topic_id=41)],
    [dict(topic(), message_thread_id=41), message()],
    [message(reply_to_message_id=32)],
    [message(32, reply_to_message_id=33), message(33, reply_to_message_id=32)],
])
def test_contradictory_topic_evidence_and_reply_cycles_fail_closed(items):
    with pytest.raises(HistoryExportError):
        parse_telegram_desktop_export(export(*items))


def test_identical_duplicate_is_counted_once_but_conflicting_duplicate_aborts():
    parsed = parse_telegram_desktop_export(export(topic(), message(), message()))
    assert len(parsed.messages) == 1
    assert parsed.duplicate_messages == 1
    with pytest.raises(HistoryExportError, match="conflicting duplicate"):
        parse_telegram_desktop_export(export(topic(), message(), message(text="Other snapshot")))


def test_semantic_checksum_ignores_export_format_names_reactions_and_local_paths():
    data = export(topic(), message(file="private/path-a.ogg", media_type="voice_message"))
    other = deepcopy(data)
    other["name"] = "Renamed group"
    other["messages"][1].update({"from": "Renamed author", "file": "private/path-b.ogg",
                                 "reactions": [{"type": "emoji", "count": 20}]})
    first = parse_telegram_desktop_export(json.dumps(data, indent=2))
    second = parse_telegram_desktop_export(json.dumps(other, separators=(",", ":")))
    assert first.export_sha256 != second.export_sha256
    assert first.messages[0].checksum == second.messages[0].checksum
    assert first.messages[0].media_reference == {}


@pytest.mark.parametrize("changes", [
    {"text": "A different source"}, {"from_id": "user7002"},
    {"date_unixtime": "1790000002"}, {"edited_unixtime": "1790000010"},
    {"forwarded_from": "Someone"}, {"message_thread_id": 41},
])
def test_semantic_checksum_changes_with_source_identity_or_body(changes):
    assert project().checksum != project(**changes).checksum


def test_manifest_digest_is_hash_of_exact_uploaded_bytes_including_bom():
    encoded = b"\xef\xbb\xbf" + json.dumps(export(), ensure_ascii=False).encode("utf-8")
    parsed = parse_telegram_desktop_export(encoded)
    assert parsed.export_sha256 == hashlib.sha256(encoded).hexdigest()


def test_forward_keeps_forwarder_identity_and_marks_external_evidence():
    item = project(forwarded_from="Original synthetic author", forwarded_from_id="user9999")
    assert item.author_user_id == 7001
    assert item.contains_external_content


def test_channel_anonymous_and_explicit_bot_authors_are_counted_as_skipped():
    parsed = parse_telegram_desktop_export(export(
        topic(), message(32, from_id="channel9000"), message(33, from_id=None),
        message(34, is_bot=True), message(35),
    ))
    assert [m.telegram_message_id for m in parsed.messages] == [35]
    assert parsed.skipped_counts == {"service": 1, "unidentified_author": 2, "bot_author": 1}


def test_formatted_text_keeps_display_text_not_urls_or_entity_metadata():
    item = project(text=["Read ", {"type": "bold", "text": "this"}, " ",
                         {"type": "text_link", "text": "label", "href": "private-link"},
                         {"type": "custom_emoji", "text": "🙂", "document_id": "private-id"}])
    assert item.available_text == "Read this label🙂"
    assert item.body_coverage == "full"


def test_rich_paragraphs_lists_and_formatting_preserve_text_and_mark_projection_partial():
    rich = {"part": False, "blocks": [
        {"type": "paragraph", "text": {"type": "concat", "text": [
            {"type": "custom_emoji", "text": "🧩", "document_id": "private-id"},
            {"type": "bold", "text": {"type": "plain", "text": " Plan"}},
        ]}},
        {"type": "list", "kind": "ordered", "items": [
            {"num": "3", "content": "text", "task_state": "checked",
             "text": {"type": "plain", "text": "One"}},
            {"num": "4", "content": "text", "task_state": "unchecked",
             "text": {"type": "italic", "text": {"type": "plain", "text": "Two"}}},
        ]},
        {"type": "paragraph", "text": {"type": "empty"}},
    ]}
    item = project(text="", rich_message=rich)
    assert item.available_text == "🧩 Plan\n\n3. [x] One\n4. [ ] Two\n\n"
    assert item.content_kind == "rich_message"
    assert item.body_coverage == "partial"


def test_unknown_rich_nodes_are_not_recursively_misread_as_source_text():
    rich = {"blocks": [
        {"type": "paragraph", "text": {"type": "plain", "text": "Known"}},
        {"type": "future_widget", "text": "Do not invent support", "url": "private"},
    ]}
    parsed = parse_telegram_desktop_export(export(topic(), message(text="", rich_message=rich)))
    assert parsed.messages[0].available_text == "Known"
    assert parsed.messages[0].body_coverage == "partial"
    assert parsed.warning_counts == {"unsupported_text_nodes": 1}


def test_unknown_only_rich_or_plain_entity_body_is_reported_unavailable():
    for changes in ({"text": [{"type": "future_entity", "text": "unverified"}]},
                    {"text": "", "rich_message": {"blocks": [{"type": "future_widget"}]}}):
        parsed = parse_telegram_desktop_export(export(topic(), message(**changes)))
        assert parsed.messages[0].available_text is None
        assert parsed.messages[0].body_coverage == "unavailable"
        assert parsed.warning_counts == {"unsupported_text_nodes": 1, "body_unavailable": 1}


def test_quotes_are_external_in_text_entities_and_rich_blocks():
    assert project(text_entities=[{"type": "blockquote", "text": "Quoted"}]).contains_external_content
    assert project(text=[{"type": "blockquote", "text": "Quoted"}]).contains_external_content
    assert project(text="", rich_message={"blocks": [
        {"type": "quote", "text": {"type": "plain", "text": "Quoted"}},
    ]}).contains_external_content


def test_media_is_never_read_and_absent_transcript_is_not_claimed_as_full():
    parsed = parse_telegram_desktop_export(export(topic(), message(text="", file="unread.ogg",
                                                                   media_type="voice_message")))
    item = parsed.messages[0]
    assert item.content_kind == "voice"
    assert item.available_text is None
    assert item.body_coverage == "unavailable"
    assert parsed.warning_counts == {"body_unavailable": 1}
    caption = project(text="Photo caption", photo="unread.jpg")
    assert caption.available_text == "Photo caption"
    assert caption.content_kind == "photo"
    assert caption.body_coverage == "partial"


@pytest.mark.parametrize("changes", [
    {"date_unixtime": None}, {"date_unixtime": True}, {"date_unixtime": 0},
    {"date_unixtime": "-1"}, {"date_unixtime": "1790000000.5"},
    {"date_unixtime": "999999999999"}, {"date_unixtime": 1790000000.5},
    {"edited": "locale-only"}, {"edited_unixtime": "1790000000"},
    {"edited_unixtime": False}, {"reply_to_message_id": True},
    {"message_thread_id": "31"}, {"id": 0}, {"from_id": "user0"},
    {"is_bot": "false"}, {"text": None}, {"text_entities": "not-list"},
    {"media_type": []}, {"rich_message": []},
    {"rich_message": {"blocks": []}},
])
def test_invalid_identity_dates_or_competing_bodies_are_rejected(changes):
    with pytest.raises(HistoryExportError):
        project(**changes)


@pytest.mark.parametrize("payload", [
    b"not-json", b"\xff", b'[]', b'{"id":1,"id":2}', b'{"id":NaN}',
    export(type="personal_chat"), export(type=[]), export(id=True),
    export(id=-7000), export(messages={}), export(messages=[None]),
])
def test_malformed_and_non_group_exports_are_rejected(payload):
    with pytest.raises(HistoryExportError):
        parse_telegram_desktop_export(payload)


def test_wrong_destination_is_rejected_before_returning_any_message():
    with pytest.raises(HistoryExportError, match="different chat"):
        parse_telegram_desktop_export(export(), expected_chat_id=-1000000007001)


def test_parser_enforces_file_record_text_and_nesting_bounds(monkeypatch):
    monkeypatch.setattr(telegram_history, "_MAX_BYTES", 10)
    with pytest.raises(HistoryExportError, match="file-size"):
        parse_telegram_desktop_export(export())
    monkeypatch.setattr(telegram_history, "_MAX_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(telegram_history, "_MAX_MESSAGES", 1)
    with pytest.raises(HistoryExportError, match="record-count"):
        parse_telegram_desktop_export(export())
    monkeypatch.setattr(telegram_history, "_MAX_MESSAGES", 100000)
    monkeypatch.setattr(telegram_history, "_MAX_TEXT", 5)
    with pytest.raises(HistoryExportError, match="text bound"):
        project()
    monkeypatch.setattr(telegram_history, "_MAX_TEXT", 1000000)
    nested = {"type": "plain", "text": "Synthetic"}
    for _ in range(70):
        nested = {"type": "bold", "text": nested}
    with pytest.raises(HistoryExportError, match="nesting"):
        project(text=[nested])


def test_historical_records_are_immutable_and_never_include_desktop_file_references():
    item = project()
    with pytest.raises(FrozenInstanceError):
        item.available_text = "Changed"
    with pytest.raises(TypeError):
        item.media_reference["file_id"] = "fabricated"
    with pytest.raises(HistoryExportError):
        replace(item, media_reference={"file_id": "fabricated"})
    with pytest.raises(HistoryExportError):
        replace(item, occurred_at=datetime(2026, 1, 1))


def test_unpaired_surrogate_is_rejected_as_invalid_unicode():
    with pytest.raises(HistoryExportError, match="Unicode"):
        project(text="\ud800")
    raw = json.dumps(export(topic(), message(text="\ud800")))
    with pytest.raises(HistoryExportError, match="Unicode"):
        parse_telegram_desktop_export(raw)
