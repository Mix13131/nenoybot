import pytest

from app_v2.services.coach_controls import CoachControls, parse_control


@pytest.mark.parametrize("text,expected", [
    ("/archive_on","archive_on"),("память вкл","archive_on"),
    ("/history off","history_off"),("авто выкл","auto_off"),
    ("/direct_on","direct_on"),("/track start","track_start"),
    ("/track_pause","track_pause"),("/track delete","track_delete"),
    ("стоп","stop"),("забудь мои сообщения","forget"),
    ("/privacy","privacy"),("/status","status"),
    ("включи всем память",None),("да",None),("/archive_on 7002",None),
    ("Он сказал /archive_on",None),(None,None),
])
def test_controls_are_explicit_and_cannot_target_another_person(text,expected):
    assert parse_control(text)==expected


def test_configured_alias_and_username_do_not_enable_other_bots_commands():
    assert parse_control("/archive_on@sample_bot",bot_username="sample_bot")=="archive_on"
    assert parse_control("/archive_on@other_bot",bot_username="sample_bot") is None
    assert parse_control("@sample_bot память вкл",bot_username="sample_bot")=="archive_on"
    assert parse_control("Собеседник, память вкл",aliases=("Собеседник",))=="archive_on"
    assert parse_control("НеНой, память вкл",aliases=("Собеседник",)) is None


def test_retention_policy_cannot_be_implicitly_selected():
    with pytest.raises(TypeError):
        CoachControls(object())
    with pytest.raises(ValueError):
        CoachControls(object(),archive_policy="")


def test_disclosure_explains_scope_publication_lifetime_and_deletion_limits():
    class FakeRepo:
        transient_ttl_seconds=86400
    text=CoachControls(FakeRepo(),archive_policy="until_author_deletes").disclosure_text()
    for phrase in ("только ваши новые сообщения", "этой теме этой группы", "до вашего удаления",
                   "Ответы по запросу можно включить без архива", "виден участникам", "24 ч",
                   "не удаляет сообщения Telegram", "прежние резервные копии", "ещё раз"):
        assert phrase in text
