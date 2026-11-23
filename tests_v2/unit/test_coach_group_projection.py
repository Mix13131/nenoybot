"""Synthetic group/root projection without weakening the default Stage A path."""
from copy import deepcopy

import pytest

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.repositories.source_archive_repo import SourceArchiveRepository


CHAT = -9000701
AUTHOR = 9000702


def update(*, kind="supergroup", forum=True, **changes):
    chat = {"id": CHAT, "type": kind}
    if forum is not None:
        chat["is_forum"] = forum
    message = {"message_id": 11, "date": 1_790_000_000,
               "from": {"id": AUTHOR, "is_bot": False}, "chat": chat,
               "text": "Синтетический отчёт без потери первого сообщения.", **changes}
    return {"update_id": 1001, "message": message}


def project(event, *, joined=True):
    normalized = normalize_update(event)
    return (archive_message_from_update(normalized, allow_group_messages=joined)
            if normalized is not None else None)


@pytest.mark.parametrize("kind,forum,fields", [
    ("supergroup", True, {}),
    ("supergroup", True, {"is_topic_message": False}),
    ("supergroup", True, {"message_thread_id": 1}),
    ("supergroup", True, {"message_thread_id": 1, "is_topic_message": True}),
    ("supergroup", True, {"message_thread_id": 23}),
    ("supergroup", True, {"message_thread_id": 23, "is_topic_message": False}),
    ("supergroup", False, {}),
    ("supergroup", None, {}),
    ("supergroup", False, {"message_thread_id": 23}),
    ("group", None, {}),
    ("group", False, {"message_thread_id": 23, "is_topic_message": False}),
])
def test_joined_group_root_preserves_received_identity_and_never_mutates_input(kind, forum, fields):
    event = update(kind=kind, forum=forum, **fields)
    before = deepcopy(event)
    source = project(event)
    assert source is not None
    assert (source.chat_id, source.message_thread_id, source.author_user_id) == (CHAT, 1, AUTHOR)
    assert source.available_text == before["message"]["text"]
    assert source.source_id == f"tgsrc:{CHAT}:1:11:1001"
    assert event == before


@pytest.mark.parametrize("kind,forum", [("group", None), ("supergroup", False), ("supergroup", True)])
def test_stage_a_default_still_requires_an_explicit_forum_topic(kind, forum):
    event = update(kind=kind, forum=forum)
    assert project(event, joined=False) is None
    assert project(event, joined=True) is not None


def test_existing_forum_archive_source_and_checksum_remain_identical():
    event = update(message_thread_id=17, is_topic_message=True)
    original, joined = project(event, joined=False), project(event)
    assert original is not None and joined is not None
    assert original == joined
    assert original.source_id == joined.source_id and original.checksum == joined.checksum
    assert joined.message_thread_id == 17


@pytest.mark.parametrize("kind,forum,fields", [
    ("private", None, {}), ("channel", None, {}),
    ("group", True, {}), ("group", None, {"is_topic_message": True, "message_thread_id": 17}),
    ("supergroup", False, {"is_topic_message": True, "message_thread_id": 17}),
    ("supergroup", True, {"is_topic_message": True}),
    ("supergroup", True, {"message_thread_id": 0}),
    ("supergroup", True, {"message_thread_id": -17}),
    ("supergroup", True, {"message_thread_id": True}),
    ("supergroup", True, {"message_thread_id": "17"}),
    ("supergroup", True, {"message_thread_id": None}),
    ("supergroup", True, {"is_topic_message": 1}),
    ("supergroup", True, {"is_topic_message": None}),
    ("supergroup", "true", {}),
])
def test_other_chats_and_contradictory_topic_fields_are_not_reinterpreted(kind, forum, fields):
    assert project(update(kind=kind, forum=forum, **fields)) is None


@pytest.mark.parametrize("fields", [
    {"from": {"id": AUTHOR, "is_bot": True}},
    {"from": {"id": AUTHOR}},
    {"sender_chat": {"id": CHAT}},
    {"ephemeral_message_id": 2},
    {"receiver_user": {"id": AUTHOR}},
    {"guest_query_id": "synthetic-other-context"},
    {"business_connection_id": "synthetic-business-context"},
    {"sender_business_bot": {"id": 9000703, "is_bot": True}},
])
def test_broad_group_policy_keeps_author_and_audience_boundaries(fields):
    assert project(update(**fields)) is None


def test_nested_reply_never_creates_a_scope_or_copies_third_party_text():
    source = project(update(reply_to_message={
        "message_id": 17, "message_thread_id": 19, "is_topic_message": True,
        "chat": {"id": CHAT - 1, "type": "supergroup", "is_forum": True},
        "from": {"id": AUTHOR + 1, "is_bot": False}, "text": "THIRD_PARTY_SECRET",
    }))
    assert source is not None and source.message_thread_id == 1
    assert source.reply_to_message_id == 17 and source.author_user_id == AUTHOR
    assert "THIRD_PARTY_SECRET" not in repr(source)


def test_quote_is_captured_as_external_content_instead_of_authors_own_evidence():
    source = project(update(quote={"text": "THIRD_PARTY_SECRET"}))
    assert source is not None and source.contains_external_content
    assert "THIRD_PARTY_SECRET" not in repr(source)


def test_archive_repository_requires_explicit_boolean_group_policy():
    assert SourceArchiveRepository(None).allow_group_messages is False
    assert SourceArchiveRepository(None, allow_group_messages=True).allow_group_messages is True
    with pytest.raises(ValueError, match="explicit"):
        SourceArchiveRepository(None, allow_group_messages="yes")
