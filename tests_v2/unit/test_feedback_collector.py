from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.feedback_repo import FeedbackRecord
from app_v2.services.feedback_collector import FeedbackCollector


NOW=datetime(2026,9,14,10,0,tzinfo=timezone.utc)


def evt(event_type, *, event_id="tg:1", text=None, message_id="88", reply_to=None, metadata=None):
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id=message_id,
        reply_to_message_id=reply_to,
        text=text,
        metadata=metadata or {},
    )


class FakeRepo:
    def __init__(self):
        self.by_id={}
        self.bot_messages={88:42}
        self.latest=41
        self.bot_lookup=[]
        self.latest_lookup=[]

    def find_intervention_by_bot_message(self, scope_id, message_id):
        self.bot_lookup.append((scope_id,int(message_id)))
        return self.bot_messages.get(int(message_id))

    def latest_intervention(self, scope_id, *, before, max_age_hours=24):
        self.latest_lookup.append((scope_id,before,max_age_hours))
        return self.latest

    def record(self, feedback, *, intervention_id=None):
        existing=self.by_id.get(feedback.feedback_id)
        if existing:
            return FeedbackRecord(existing.id,feedback.feedback_id,existing.intervention_id,existing.feedback_type,False)
        record=FeedbackRecord(len(self.by_id)+1,feedback.feedback_id,intervention_id,feedback.feedback_type,True)
        self.by_id[feedback.feedback_id]=record
        return record


def test_positive_reaction_is_attached_to_sent_bot_intervention() -> None:
    repo=FakeRepo(); collector=FeedbackCollector(repo)
    event=evt(
        EventType.REACTION_ADDED,
        metadata={"old_reaction":[],"new_reaction":[{"type":"emoji","emoji":"🔥"}]},
    )
    result=collector.collect(event)
    assert result.feedback is not None
    assert result.feedback.feedback_type == "reaction_positive"
    assert result.feedback.value == 1.0
    assert result.record is not None and result.record.intervention_id == 42
    assert repo.bot_lookup == [("-100777",88)]


def test_negative_reaction_is_normalized_for_adaptive_policy() -> None:
    result=FeedbackCollector(FakeRepo()).collect(evt(
        EventType.REACTION_ADDED,
        metadata={"new_reaction":[{"type":"emoji","emoji":"👎"}]},
    ))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "reaction_negative"
    assert result.feedback.value == -1.0


def test_reaction_removed_is_raw_feedback() -> None:
    result=FeedbackCollector(FakeRepo()).collect(evt(
        EventType.REACTION_REMOVED,
        metadata={"old_reaction":[{"type":"emoji","emoji":"👍"}],"new_reaction":[]},
    ))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "reaction_removed"
    assert result.feedback.payload["old_emoji"] == ["👍"]


def test_reply_to_bot_resolves_using_replied_telegram_message() -> None:
    repo=FakeRepo(); collector=FeedbackCollector(repo)
    result=collector.collect(evt(EventType.REPLY_TO_BOT,text="а вот тут?",message_id="99",reply_to="88"))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "reply_to_bot"
    assert result.record is not None and result.record.intervention_id == 42


def test_organic_remention_links_to_recent_intervention() -> None:
    repo=FakeRepo(); result=FeedbackCollector(repo).collect(evt(EventType.DIRECT_MENTION,text="@nenoy а ты?"))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "organic_remention"
    assert result.record is not None and result.record.intervention_id == 41


def test_explicit_negative_is_distinct_from_regular_reply() -> None:
    result=FeedbackCollector(FakeRepo()).collect(evt(EventType.REPLY_TO_BOT,text="не смешно",reply_to="88"))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "explicit_negative"
    assert result.feedback.value == -1.0


def test_mute_feedback_is_distinct_and_keeps_raw_text() -> None:
    result=FeedbackCollector(FakeRepo()).collect(evt(EventType.DIRECT_MENTION,text="@nenoy заткнись"))
    assert result.feedback is not None
    assert result.feedback.feedback_type == "mute"
    assert result.feedback.payload["text"] == "@nenoy заткнись"


def test_same_reaction_event_is_idempotent() -> None:
    repo=FakeRepo(); collector=FeedbackCollector(repo)
    event=evt(EventType.REACTION_ADDED,event_id="tg:555",metadata={"new_reaction":[{"type":"emoji","emoji":"👍"}]})
    first=collector.collect(event)
    second=collector.collect(event)
    assert first.record is not None and first.record.created is True
    assert second.record is not None and second.record.created is False
    assert first.record.id == second.record.id
    assert len(repo.by_id) == 1


def test_unrelated_group_message_is_not_recorded_as_feedback() -> None:
    result=FeedbackCollector(FakeRepo()).collect(evt(EventType.GROUP_MESSAGE,text="просто сообщение"))
    assert result.feedback is None
    assert result.record is None
