from datetime import UTC, date, datetime, timedelta

import pytest

from app.memory_store import InMemoryStore
from app.support import SupportSettings, due_slots, load_catalog, local_slot, next_run, parse_schedule


def test_v4_catalog_contract() -> None:
    catalog = load_catalog()
    assert len(catalog) == 36
    assert len({item["id"] for item in catalog}) == 36
    assert len({item["text"] for item in catalog}) == 36
    assert {item["category"] for item in catalog} == {"M", "D", "A"}
    assert sum("?" in item["text"] for item in catalog) <= 12
    forbidden = ("поймай лучик", "обними себя", "ты достоин", "тёплое пространство")
    assert not any(word in item["text"].casefold() for item in catalog for word in forbidden)

    semantic_emojis = (
        "🎮", "🧩", "👀", "😎", "💬", "🔎", "🌿", "🎯", "😬", "🙂", "🔧", "⏱️",
        "🧠", "🛠️", "✅", "😏", "📋", "🎭", "🧪", "🎲", "📊", "🤝", "❓", "📦",
        "🔄", "💡", "🚫", "⚖️", "📈", "🔥", "🎓", "📜", "🗺️", "💪", "💰", "🎬",
        "🗿", "😇", "🚀",
    )
    for item in catalog:
        text = item["text"]
        assert "\n\n" in text
        assert sum(text.count(emoji) for emoji in semantic_emojis) >= 2
        assert any(
            0 < text.find(emoji) < len(text) - len(emoji)
            for emoji in semantic_emojis
            if emoji in text
        )


def test_schedule_validation_does_not_mutate_store() -> None:
    store = InMemoryStore()
    store.configure_support(1, ("09:00",), "Europe/Moscow")
    with pytest.raises(ValueError):
        parse_schedule("25:00 Europe/Moscow")
    assert store.get_support_settings(1).times == ("09:00",)


def test_dst_gap_is_skipped_and_fold_is_one_slot() -> None:
    assert local_slot(date(2026, 3, 29), "02:30", "Europe/Berlin") is None
    assert local_slot(date(2026, 10, 25), "02:30", "Europe/Berlin") is not None


def test_pause_and_next_run_use_local_calendar() -> None:
    settings = SupportSettings(1, "support", True, "Europe/Moscow", ("09:00",), date(2026, 9, 12))
    now = datetime(2026, 9, 11, 5, 30, tzinfo=UTC)
    assert next_run(settings, now) == datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    assert due_slots(settings, datetime(2026, 9, 11, 6, 0, tzinfo=UTC)) == []


def test_fourteen_days_recur_without_incoming_messages_and_dedupe() -> None:
    store = InMemoryStore()
    store.set_mode(7, "support")
    store.configure_support(7, ("09:00", "13:00", "17:00"), "UTC")
    start = datetime(2026, 8, 31, tzinfo=UTC)  # Monday; range includes weekends.
    sent = []
    for day in range(14):
        for hour in (9, 13, 17):
            now = start + timedelta(days=day, hours=hour)
            slots = store.claim_support_slots(now)
            sent.extend(slots)
            assert store.claim_support_slots(now) == []
            for slot in slots:
                store.complete_support_slot(slot, "confirmed", 100 + len(sent))
    assert len(sent) == 42
    for index, slot in enumerate(sent):
        assert slot.content_id not in {old.content_id for old in sent[max(0, index-20):index]}


def test_coach_mode_suppresses_support_delivery_but_keeps_opt_in() -> None:
    store = InMemoryStore()
    store.configure_support(1, ("09:00",), "UTC")
    assert store.claim_support_slots(datetime(2026, 9, 11, 9, 0, tzinfo=UTC)) == []
    assert store.get_support_settings(1).enabled
