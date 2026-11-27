"""Unsolicited assistant admission is selective; archival has no relevance filter."""
from types import SimpleNamespace

import pytest

from app_v2.coach_config import CoachConfig
from app_v2.services.coach_ingestor import (
    is_coach_addressed, unsolicited_coaching_candidate,
)

BOT=909090002
CONFIG=CoachConfig(bot_user_id=BOT, bot_username="arnys_bot", aliases=("арни",))


def update(text, *, replied_to=None):
    message = {"text": text}
    if replied_to is not None:
        message["reply_to_message"] = replied_to
    return SimpleNamespace(message=message)


def human_reply():
    return {"message_id": 99, "from": {"id": 999999111, "is_bot": False},
            "text": "Смотри ролик про публичный продукт"}


@pytest.mark.parametrize("text", [
    "Да, слышал про такое",
    "Согласен",
    "Спасибо!",
    "Ага, крутая штука",
    "https://www.instagram.com/reel/fake_url/",
    "Яндекс пин, найди всю инфу",
    "Глянь ролик https://example.com/public",
    "Нужно вернуться завтра к этой теме.",
])
def test_regular_remarks_and_link_shares_do_not_trigger_coach(text):
    assert not unsolicited_coaching_candidate(update(text), CONFIG)


def test_very_long_public_link_share_is_not_mistaken_for_personal_reflection():
    text = "Сегодня я прочитал интересный обзор, делюсь ссылкой с вами. " * 10
    assert not unsolicited_coaching_candidate(
        update(text + "https://example.com/article"), CONFIG)


@pytest.mark.parametrize("text", [
    "Отчёт: закончил собственный черновик.",
    "Вечерний разбор | 25.11.2026\nСегодня я сделал намеченное.",
    "25.11\n1. Что сделал сегодня\nПровёл три звонка.",
    "🗓 Отчёт за 25.11.2026\nЧто получилось сегодня",
])
def test_structured_daily_reports_remain_eligible_for_proactive_feedback(text):
    assert unsolicited_coaching_candidate(update(text), CONFIG)


def test_substantial_standalone_reflection_remains_eligible():
    text = ("Сегодня я заметил, что слишком часто переключаюсь между задачами. "
            "Два часа ушло на мелкие уведомления, а важный разговор перенёс на завтра. "
            "Я решил попробовать иначе: сначала завершить один разговор, затем "
            "отвечать в переписке. Мне важно увидеть результат этого изменения.")
    assert len(text) > 240
    assert unsolicited_coaching_candidate(update(text), CONFIG)


def test_report_replying_to_a_human_is_not_a_proactive_invitation():
    text = "Отчёт: " + "Сегодня я заметил, как изменилась моя работа. " * 12
    assert not unsolicited_coaching_candidate(update(text, replied_to=human_reply()), CONFIG)


def test_forum_service_root_is_not_a_human_reply():
    report = update("Отчёт: завершил работу.", replied_to={
        "message_id": 3, "forum_topic_created": {"name": "Отчёты за день"}})
    assert unsolicited_coaching_candidate(report, CONFIG)


def test_explicit_direct_request_to_bot_survives_a_human_reply():
    user = update("Арни, помоги оценить мой отчёт", replied_to=human_reply())
    assert is_coach_addressed(user, CONFIG)
    assert not unsolicited_coaching_candidate(user, CONFIG)


def test_reply_to_bot_is_direct_even_without_name():
    user = update("Сделаю завтра", replied_to={
        "message_id": 199, "from": {"id": BOT, "is_bot": True}})
    assert is_coach_addressed(user, CONFIG)


def test_forum_root_from_human_is_still_a_topic_root_not_an_active_reply():
    message = SimpleNamespace(message={
        "text": "Отчёт: закончил рабочий день.",
        "is_topic_message": True, "message_thread_id": 3,
        "reply_to_message": {"message_id": 3,
                             "from": {"id": 88881111, "is_bot": False},
                             "forum_topic_created": {"name": "Отчёты за день"}},
    })
    assert unsolicited_coaching_candidate(message, CONFIG)

@pytest.mark.parametrize("text", [
    "Сегодня я понял, что план на день не сработал, потому что отвлекался на сообщения.",
    "Я попробовал сначала закрывать сложную задачу, теперь успеваю больше.",
    "У меня не получается довести важный проект до конца, всё время переношу.",
    "Сегодня нам сложно договориться о сроках, и это тормозит общий результат.",
    "Как нам лучше распределить работу, если сроки постоянно срываются?",
    "Почему я постоянно откладываю важные задачи, хотя они уже срочные?",
    "У меня проблема с планированием поставок: сроки начали сдвигаться.",
])
def test_meaningful_short_questions_obstacles_and_progress_admit_autonomous_coach(text):
    assert len(text) < 240
    assert unsolicited_coaching_candidate(update(text), CONFIG)


@pytest.mark.parametrize("text", [
    "Ага, понял.",
    "Как дела?",
    "Да, слышал про такое",
    "Да, согласен, давай позже обсудим",
    "Окей, держи ссылку https://example.com/reel/123",
    "Я сегодня просто зашёл в магазин",
])
def test_casual_or_social_messages_still_do_not_invite_unsolicited_coaching(text):
    assert not unsolicited_coaching_candidate(update(text), CONFIG)


def test_meaningful_question_in_human_reply_does_not_interrupt_people():
    text="Как нам лучше распределить задачи, если сроки всё время срываются?"
    assert not unsolicited_coaching_candidate(update(text, replied_to=human_reply()), CONFIG)
