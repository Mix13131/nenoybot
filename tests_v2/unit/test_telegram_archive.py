"""Synthetic archive projection: identity, coverage and immutable provenance."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.domain.source_archive import ArchiveMessage


def source_update(*, update_id=1001, edited=False, **changes):
    message = {
        "message_id": 11,
        "date": 1_790_000_000,
        "from": {"id": 7001, "is_bot": False, "first_name": "Synthetic A"},
        "chat": {"id": -1007000, "type": "supergroup", "is_forum": True},
        "message_thread_id": 31,
        "is_topic_message": True,
        "text": "  Синтетический отчёт\nбез изменения пробелов.  ",
    }
    message.update(changes)
    return {"update_id": update_id, "edited_message" if edited else "message": message}


def project(update=None, **changes):
    normalized = normalize_update(update or source_update(**changes))
    assert normalized is not None
    return archive_message_from_update(normalized)


def test_text_preserves_exact_received_source_and_stable_identity():
    update = source_update()
    archived = project(update)
    assert archived is not None
    assert archived.available_text == update["message"]["text"]
    assert archived.source_id == "tgsrc:-1007000:31:11:1001"
    assert archived.author_user_id == 7001
    assert archived.content_kind == "text"
    assert archived.body_coverage == "full"
    assert archived.revision_at == archived.occurred_at
    assert archived.occurred_at == datetime.fromtimestamp(1_790_000_000, timezone.utc)
    assert not archived.is_edited
    assert not archived.contains_external_content
    assert archived.checksum == project(update).checksum


def test_edit_keeps_creation_time_and_uses_edit_timestamp_for_revision():
    archived = project(edited=True, edit_date=1_790_000_100)
    original = project()
    assert archived is not None
    assert archived.occurred_at == original.occurred_at
    assert archived.revision_at == original.occurred_at + timedelta(seconds=100)
    assert archived.is_edited
    assert archived.checksum != original.checksum


@pytest.mark.parametrize("changes", [
    {"chat": {"id": 7001, "type": "private", "is_forum": True}},
    {"chat": {"id": -1007000, "type": "channel", "is_forum": True}},
    {"chat": {"id": -1007000, "type": "group", "is_forum": True}},
    {"chat": {"id": -1007000, "type": "supergroup"}},
    {"chat": {"id": -1007000, "type": "supergroup", "is_forum": False}},
    {"is_topic_message": False},
    {"is_topic_message": None},
    {"message_thread_id": None},
    {"message_thread_id": True},
    {"message_thread_id": "31"},
    {"message_thread_id": 0},
    {"message_thread_id": -31},
    {"from": {"id": 7001, "is_bot": True}},
    {"from": {"id": 7001}},
    {"from": {"id": True, "is_bot": False}},
    {"from": {"id": "7001", "is_bot": False}},
    {"from": None},
    {"sender_chat": {"id": -1007000}},
    {"sender_chat": None},
    {"message_id": True},
    {"message_id": 0},
    {"message_id": "11"},
    {"ephemeral_message_id": "private-audience"},
    {"receiver_user": {"id": 7002}},
])
def test_uncertain_identity_topic_and_limited_audience_fail_closed(changes):
    assert project(**changes) is None


def test_unknown_topic_is_not_inferred_from_reply_or_text():
    assert project(
        message_thread_id=None,
        text="Тема: Отчёты #31",
        reply_to_message={"message_id": 31, "message_thread_id": 31},
    ) is None


def test_normalized_routing_mismatch_is_rejected():
    normalized = normalize_update(source_update())
    other_author = normalized.envelope.model_copy(update={"actor_user_id": "7002"})
    assert archive_message_from_update(replace(normalized, envelope=other_author)) is None
    other_chat = normalized.envelope.model_copy(update={"scope_id": "-1007001"})
    assert archive_message_from_update(replace(normalized, envelope=other_chat)) is None


@pytest.mark.parametrize("update_id", [True, -1, 1 << 63])
def test_bad_update_ids_are_not_archive_identifiers(update_id):
    assert project(update_id=update_id) is None


@pytest.mark.parametrize("date", [True, False, 0, -1, 1_790_000_000.5])
def test_bad_dates_rejected_even_if_generic_normalizer_coerces_them(date):
    assert project(date=date) is None


@pytest.mark.parametrize("changes", [
    {"edited": True},
    {"edited": True, "edit_date": True},
    {"edited": True, "edit_date": 1_789_999_999},
    {"edited": True, "edit_date": "1790000100"},
    {"edited": True, "edit_date": 1 << 63},
])
def test_edit_requires_a_valid_monotonic_revision_timestamp(changes):
    assert project(**changes) is None


def test_caption_and_photo_reference_are_partial_without_photo_bytes():
    archived = project(
        text=None,
        caption="  Подпись к синтетическому фото. ",
        photo=[
            {"file_id": "small", "file_unique_id": "small-u", "file_size": 10},
            {"file_id": "large", "file_unique_id": "large-u", "file_size": 100,
             "bytes": "DO NOT COPY", "file_name": "private-name.png"},
        ],
        media_group_id="album-1",
    )
    assert archived is not None
    assert archived.content_kind == "photo"
    assert archived.body_coverage == "partial"
    assert archived.available_text == "  Подпись к синтетическому фото. "
    assert archived.media_reference == {
        "file_id": "large", "file_unique_id": "large-u", "file_size": 100,
        "media_group_id": "album-1",
    }


@pytest.mark.parametrize("kind", ["voice", "audio", "video", "video_note", "document", "sticker", "animation"])
def test_binary_media_is_not_described_as_remembered(kind):
    archived = project(text=None, **{kind: {
        "file_id": "synthetic-file", "file_unique_id": "synthetic-unique", "file_size": 123,
        "file_name": "private.txt", "performer": "private-name", "thumbnail": {"file_id": "nested"},
    }})
    assert archived is not None
    assert archived.content_kind == kind
    assert archived.body_coverage == "unavailable"
    assert archived.available_text is None
    assert archived.media_reference == {
        "file_id": "synthetic-file", "file_unique_id": "synthetic-unique", "file_size": 123,
    }


@pytest.mark.parametrize("payload,kind", [
    ({"poll": {"question": "Do not copy nested content"}}, "poll"),
    ({"rich_message": {"text": "Unsupported richer source"}}, "rich_message"),
    ({"future_media_type": {"private_body": "Do not copy"}}, "unsupported"),
])
def test_unhandled_content_has_truthful_unavailable_coverage(payload, kind):
    archived = project(text=None, **payload)
    assert archived.content_kind == kind
    assert archived.available_text is None
    assert archived.body_coverage == "unavailable"
    assert archived.media_reference == {}


def test_caption_without_known_media_does_not_claim_full_coverage():
    archived = project(text=None, caption="Only the caption was captured")
    assert archived.content_kind == "caption"
    assert archived.body_coverage == "partial"


def test_two_competing_text_bodies_fail_instead_of_losing_one():
    assert project(caption="Unexpected second text body") is None


def test_reply_keeps_only_pointer_and_never_embeds_third_party_text():
    archived = project(reply_to_message={
        "message_id": 9,
        "from": {"id": 7999, "is_bot": False},
        "text": "PRIVATE THIRD PARTY BODY",
        "caption": "PRIVATE THIRD PARTY CAPTION",
    })
    assert archived.reply_to_message_id == 9
    assert archived.author_user_id == 7001
    assert "PRIVATE THIRD PARTY" not in repr(archived)
    assert not archived.contains_external_content


@pytest.mark.parametrize("changes", [
    {"forward_origin": {"type": "user", "sender_user": {"id": 7999}}},
    {"forward_from": {"id": 7999}},
    {"forward_sender_name": "Private person"},
    {"forward_date": 1_780_000_000},
    {"quote": {"text": "Private quotation"}},
    {"external_reply": {"origin": {"sender_user": {"id": 7999}}}},
    {"entities": [{"type": "blockquote", "offset": 0, "length": 10}]},
    {"caption_entities": [{"type": "expandable_blockquote", "offset": 0, "length": 10}]},
])
def test_external_content_is_flagged_without_reassigning_authorship(changes):
    archived = project(**changes)
    assert archived is not None
    assert archived.author_user_id == 7001
    assert archived.contains_external_content
    assert archived.available_text == source_update()["message"]["text"]
    assert "7999" not in repr(archived)


def test_reaction_is_not_a_new_source_message():
    normalized = normalize_update({"update_id": 3001, "message_reaction": {
        "message_id": 11, "date": 1_790_000_000,
        "chat": {"id": -1007000, "type": "supergroup"},
        "user": {"id": 7001, "is_bot": False},
        "new_reaction": [{"type": "emoji", "emoji": "👍"}],
    }})
    assert normalized is not None
    assert archive_message_from_update(normalized) is None


def test_checksum_binds_all_provenance_and_support_fields():
    archived = project()
    variants = [
        replace(archived, chat_id=-1007001),
        replace(archived, message_thread_id=32),
        replace(archived, telegram_message_id=12),
        replace(archived, telegram_update_id=1002),
        replace(archived, author_user_id=7002),
        replace(archived, occurred_at=archived.occurred_at + timedelta(seconds=1),
                revision_at=archived.revision_at + timedelta(seconds=1)),
        replace(archived, is_edited=True),
        replace(archived, is_edited=True, revision_at=archived.revision_at + timedelta(seconds=1)),
        replace(archived, reply_to_message_id=9),
        replace(archived, content_kind="caption", body_coverage="partial"),
        replace(archived, available_text="Corrected synthetic text"),
        replace(archived, body_coverage="partial"),
        replace(archived, contains_external_content=True),
    ]
    assert all(item.checksum != archived.checksum for item in variants)
    assert len({item.checksum for item in variants}) == len(variants)
    media = project(text=None, voice={"file_id": "test", "file_size": 100})
    assert replace(media, media_reference={"file_id": "test", "file_size": 101}).checksum != media.checksum
    assert replace(media, media_reference={"file_size": 100, "file_id": "test"}).checksum == media.checksum


def test_record_and_its_media_reference_cannot_be_mutated():
    incoming = {"file_id": "test", "file_size": 100}
    archived = project(text=None, voice=incoming)
    checksum = archived.checksum
    incoming["file_id"] = "changed externally"
    with pytest.raises(FrozenInstanceError):
        archived.author_user_id = 7002
    with pytest.raises(TypeError, match="immutable"):
        archived.media_reference["file_id"] = "changed internally"
    with pytest.raises(TypeError, match="immutable"):
        archived.media_reference.update({"file_id": "changed internally"})
    assert archived.checksum == checksum
    assert json.loads(json.dumps(archived.media_reference)) == {"file_id": "test", "file_size": 100}


@pytest.mark.parametrize("changes", [
    {"chat_id": True}, {"chat_id": 7001}, {"author_user_id": True},
    {"telegram_update_id": -1}, {"message_thread_id": 0}, {"telegram_message_id": "11"},
    {"is_edited": 1}, {"contains_external_content": "false"},
    {"occurred_at": datetime(2026, 1, 1)},
    {"revision_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3)))},
    {"body_coverage": "guessed"},
    {"body_coverage": "unavailable"},
    {"available_text": None},
    {"content_kind": "voice"},
    {"media_reference": {"file_name": "private.txt"}},
    {"media_reference": {"file_id": {"nested": "private"}}},
])
def test_domain_constructor_cannot_bypass_the_source_contract(changes):
    with pytest.raises(ValueError):
        replace(project(), **changes)
