from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.adapters.telegram_profile import TelegramBirthdate, TelegramBirthdateLookup
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.birthday_repo import BirthdayCandidate
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.birthdays import BirthdayService


NOW = datetime(2026, 9, 27, 6, 15, tzinfo=timezone.utc)


def event(text, *, event_type=EventType.DIRECT_MENTION, user_id="42"):
    return EventEnvelope(
        event_id="e1",
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id=user_id,
        message_id="10",
        text=text,
        metadata={"direct_mention": event_type is EventType.DIRECT_MENTION},
    )


def context(*, silent_until=None, profile=None):
    return GroupContext(
        internal_chat_id=1,
        telegram_chat_id="-1001",
        title="Friends",
        is_whitelisted=True,
        is_active=True,
        silent_until=silent_until,
        profile={"timezone": "Europe/Moscow", **(profile or {})},
        participant=ParticipantContext(
            telegram_user_id="42",
            display_name="Антон",
            profile={},
        ),
    )


def connector(*, birthdays=True, birthday_hour=9, timezone_name="Europe/Moscow"):
    return SimpleNamespace(
        capabilities=SimpleNamespace(birthdays=birthdays),
        behavior=SimpleNamespace(
            birthday_hour=birthday_hour,
            timezone=timezone_name,
        ),
    )


class Repo:
    def __init__(self):
        self.saved=[]
        self.cleared=[]
        self.enabled=[]
        self.lookups=[]
        self.refresh_due=False
        self.candidates=[]
        self.enqueued=[]
        self.already_congratulated=False
        self.congratulation_checks=[]

    def rollback(self):
        pass

    def save_explicit_birthday(self, **kwargs):
        self.saved.append(kwargs)
        return True

    def clear_birthday(self, **kwargs):
        self.cleared.append(kwargs)
        return True

    def set_congratulations_enabled(self, **kwargs):
        self.enabled.append(kwargs)
        return True

    def telegram_refresh_due(self, **kwargs):
        return self.refresh_due

    def record_telegram_lookup(self, **kwargs):
        self.lookups.append(kwargs)
        return True

    def list_candidates(self, limit=1000):
        return list(self.candidates)

    def already_congratulated_today(self, candidate, *, since):
        self.congratulation_checks.append((candidate, since))
        return self.already_congratulated

    def enqueue_due(self, candidate, *, local_year, now):
        self.enqueued.append((candidate, local_year, now))
        return True


class Profiles:
    def __init__(self, result):
        self.result=result
        self.calls=[]

    def get_birthdate(self, user_id):
        self.calls.append(str(user_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Groups:
    def __init__(self, ctx):
        self.ctx=ctx

    def load(self, scope_id, actor_user_id):
        return self.ctx


class Resolver:
    def __init__(self, cfg):
        self.cfg=cfg

    def resolve(self, group_context):
        return self.cfg


def service(repo=None, profile_result=None, ctx=None, cfg=None):
    repo=repo or Repo()
    profiles=Profiles(profile_result or TelegramBirthdateLookup(status="not_shared"))
    return BirthdayService(
        repo=repo,
        telegram_profile_client=profiles,
        group_context_repo=Groups(ctx or context()),
        connector_resolver=Resolver(cfg or connector()),
    ), repo, profiles


def test_parse_explicit_russian_and_numeric_self_birthdays() -> None:
    svc, _, _ = service()
    ru = svc.parse_self_birthday("НеНой, у меня день рождения 14 мая 1981", now=NOW)
    numeric = svc.parse_self_birthday("мой др — 03.10", now=NOW)

    assert ru is not None and (ru.day, ru.month, ru.year) == (14, 5, 1981)
    assert numeric is not None and (numeric.day, numeric.month, numeric.year) == (3, 10, None)


def test_parser_does_not_accept_third_party_or_impossible_date() -> None:
    svc, _, _ = service()
    assert svc.parse_self_birthday("у Лехи день рождения 14 мая", now=NOW) is None
    assert svc.parse_self_birthday("у меня день рождения 31 февраля", now=NOW) is None
    assert svc.parse_self_birthday("у меня день рождения 1 января 2030", now=NOW) is None


def test_direct_self_birthday_is_saved_in_group_scope() -> None:
    svc, repo, profiles = service()

    action = svc.observe_group_event(
        event("НеНой, у меня день рождения 14 мая"),
        group_context=context(),
        connector_config=connector(),
        now=NOW,
    )

    assert action["status"] == "succeeded"
    assert action["operation"] == "save"
    assert action["day"] == 14
    assert action["month"] == 5
    assert repo.saved[0]["scope_id"] == "-1001"
    assert repo.saved[0]["telegram_user_id"] == "42"
    assert profiles.calls == []


def test_ambient_self_birthday_is_not_treated_as_explicit_command() -> None:
    svc, repo, _ = service()

    action = svc.observe_group_event(
        event("у меня день рождения 14 мая", event_type=EventType.GROUP_MESSAGE),
        group_context=context(),
        connector_config=connector(),
        now=NOW,
    )

    assert action is None
    assert repo.saved == []


def test_user_can_disable_and_clear_birthday_behavior() -> None:
    svc, repo, _ = service()

    disabled = svc.observe_group_event(
        event("НеНой, не поздравляй меня с днем рождения"),
        group_context=context(),
        connector_config=connector(),
        now=NOW,
    )
    cleared = svc.observe_group_event(
        event("НеНой, забудь мой день рождения"),
        group_context=context(),
        connector_config=connector(),
        now=NOW,
    )

    assert disabled["operation"] == "disable_congratulations"
    assert repo.enabled[0]["enabled"] is False
    assert cleared["operation"] == "clear"
    assert len(repo.cleared) == 1


def test_stale_profile_is_refreshed_from_telegram_when_visible() -> None:
    repo=Repo()
    repo.refresh_due=True
    lookup=TelegramBirthdateLookup(
        status="available",
        birthdate=TelegramBirthdate(day=8, month=6, year=None),
    )
    svc, repo, profiles=service(repo=repo, profile_result=lookup)

    action=svc.observe_group_event(
        event("обычная реплика", event_type=EventType.GROUP_MESSAGE),
        group_context=context(),
        connector_config=connector(),
        now=NOW,
    )

    assert action is None
    assert profiles.calls == ["42"]
    assert repo.lookups[0]["status"] == "available"
    assert repo.lookups[0]["birthdate"].day == 8


def test_scanner_enqueues_when_local_date_and_hour_match() -> None:
    repo=Repo()
    repo.candidates=[
        BirthdayCandidate(
            scope_id="-1001",
            telegram_user_id="42",
            display_name="Антон",
            day=27,
            month=9,
            source="explicit",
            participant_profile={},
        )
    ]
    svc, repo, _=service(repo=repo)

    assert svc.run_once(now=NOW) is True
    assert len(repo.enqueued) == 1
    assert repo.enqueued[0][1] == 2026


def test_scanner_skips_before_hour_muted_or_disabled() -> None:
    candidate=BirthdayCandidate(
        scope_id="-1001",
        telegram_user_id="42",
        display_name="Антон",
        day=27,
        month=9,
        source="explicit",
        participant_profile={},
    )

    repo=Repo()
    repo.candidates=[candidate]
    before=datetime(2026,9,27,5,30,tzinfo=timezone.utc)
    svc, repo, _=service(repo=repo)
    assert svc.run_once(now=before) is False
    assert repo.enqueued == []

    repo2=Repo()
    repo2.candidates=[candidate]
    muted=context(silent_until=datetime(2026,9,27,8,0,tzinfo=timezone.utc))
    svc2, repo2, _=service(repo=repo2, ctx=muted)
    assert svc2.run_once(now=NOW) is False
    assert repo2.enqueued == []

    repo3=Repo()
    repo3.candidates=[candidate]
    svc3, repo3, _=service(repo=repo3, cfg=connector(birthdays=False))
    assert svc3.run_once(now=NOW) is False
    assert repo3.enqueued == []



def test_scanner_skips_scheduled_greeting_if_bot_already_congratulated_today() -> None:
    repo=Repo()
    repo.already_congratulated=True
    repo.candidates=[
        BirthdayCandidate(
            scope_id="-1001",
            telegram_user_id="42",
            display_name="Антон Треповский",
            day=27,
            month=9,
            source="explicit",
            participant_profile={},
        )
    ]
    svc, repo, _=service(repo=repo)

    assert svc.run_once(now=NOW) is False
    assert len(repo.congratulation_checks) == 1
    assert repo.enqueued == []
    _, since = repo.congratulation_checks[0]
    assert since == datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)
