from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.repositories.group_silence_wakeup_repo import SilenceWakeupCandidate
from app_v2.services.group_silence_wakeup import GroupSilenceWakeupService


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)  # 15:00 Europe/Moscow


def candidate(**overrides):
    base = {
        "scope_id": "-100777",
        "profile": {
            "profile": "friends",
            "unsolicited_enabled": True,
            "initiative": 3,
            "timezone": "Europe/Moscow",
            "silence_wakeup_enabled": True,
            "silence_wakeup_after_minutes": 180,
            "silence_wakeup_start_hour": 10,
            "silence_wakeup_end_hour": 22,
            "silence_wakeup_daily_limit": 1,
            "silence_wakeup_attempt_gap_minutes": 30,
        },
        "silent_until": None,
        "last_human_message_id": 77,
        "last_human_message_at": NOW - timedelta(hours=4),
        "last_human_excerpt": "ну всё, разбежались по делам",
        "last_successful_wakeup_message_id": None,
        "last_attempt_message_id": None,
    }
    base.update(overrides)
    return SilenceWakeupCandidate(**base)


class Repo:
    def __init__(self, item=None, *, successful_today=0):
        self.item = item or candidate()
        self.successful_today = successful_today
        self.enqueued = []

    def list_candidates(self, *, limit=50):
        return [self.item]

    def count_successful_since(self, scope_id, since):
        return self.successful_today

    def enqueue(self, item, *, now, silence_minutes):
        self.enqueued.append((item, now, silence_minutes))
        return True


class ContextRepo:
    def load(self, scope_id, actor_user_id):
        return GroupContext(
            internal_chat_id=1,
            telegram_chat_id=scope_id,
            title="Friends",
            is_whitelisted=True,
            is_active=True,
            silent_until=None,
            profile=dict(candidate().profile),
            participant=ParticipantContext(telegram_user_id=None),
        )


class Initiative:
    def __init__(self, **overrides):
        values = {
            "group_muted": False,
            "cooldown_active": False,
            "negative_feedback_recent": 0,
            "unsolicited_today": 0,
            "soft_daily_limit": 6,
            "initiative_level": 3,
        }
        values.update(overrides)
        self.snapshot = SimpleNamespace(**values)
        self.calls = []

    def evaluate(self, *, event, group_context, now):
        self.calls.append((event, group_context, now))
        return self.snapshot


def service(repo=None, initiative=None):
    return GroupSilenceWakeupService(
        repo=repo or Repo(),
        group_context_repo=ContextRepo(),
        initiative_service=initiative or Initiative(),
    )


def test_eligible_silence_enqueues_one_durable_wakeup():
    repo = Repo()

    assert service(repo=repo).run_once(now=NOW) is True

    assert len(repo.enqueued) == 1
    _, _, silence_minutes = repo.enqueued[0]
    assert silence_minutes == 240


def test_same_silence_episode_never_gets_second_successful_wakeup():
    item = candidate(last_successful_wakeup_message_id=77)
    repo = Repo(item)

    assert service(repo=repo).run_once(now=NOW) is False
    assert repo.enqueued == []


def test_new_human_message_reopens_future_silence_episode():
    item = candidate(
        last_human_message_id=78,
        last_human_message_at=NOW - timedelta(hours=4),
        last_successful_wakeup_message_id=77,
    )
    repo = Repo(item)

    assert service(repo=repo).run_once(now=NOW) is True


def test_any_prior_attempt_in_same_silence_episode_is_not_repeated():
    item = candidate(last_attempt_message_id=77)
    repo = Repo(item)

    assert service(repo=repo).run_once(now=NOW) is False


def test_attempt_before_new_human_message_does_not_block_new_episode():
    item = candidate(
        last_human_message_id=78,
        last_human_message_at=NOW - timedelta(hours=4),
        last_attempt_message_id=77,
    )
    repo = Repo(item)

    assert service(repo=repo).run_once(now=NOW) is True


def test_malformed_enablement_value_fails_closed():
    profile = dict(candidate().profile)
    profile["silence_wakeup_enabled"] = "disabled"
    repo = Repo(candidate(profile=profile))

    assert service(repo=repo).run_once(now=NOW) is False


def test_wakeup_fails_closed_without_group_timezone():
    profile = dict(candidate().profile)
    profile.pop("timezone")
    repo = Repo(candidate(profile=profile))

    assert service(repo=repo).run_once(now=NOW) is False


def test_wakeup_stays_inside_local_active_window():
    early_utc = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)  # 07:00 Moscow
    repo = Repo(candidate(last_human_message_at=early_utc - timedelta(hours=4)))

    assert service(repo=repo).run_once(now=early_utc) is False


def test_daily_success_cap_blocks_second_reengagement():
    repo = Repo(successful_today=1)

    assert service(repo=repo).run_once(now=NOW) is False


def test_mute_cooldown_negative_and_soft_limit_all_fail_closed():
    cases = (
        {"group_muted": True},
        {"cooldown_active": True},
        {"negative_feedback_recent": 1},
        {"unsolicited_today": 6, "soft_daily_limit": 6},
        {"initiative_level": 0},
    )
    for overrides in cases:
        repo = Repo()
        assert service(repo=repo, initiative=Initiative(**overrides)).run_once(now=NOW) is False
        assert repo.enqueued == []
