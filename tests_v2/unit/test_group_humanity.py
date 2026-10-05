from app_v2.services.group_humanity import classify_group_humanity


def test_social_repair_is_short_and_detected() -> None:
    signal = classify_group_humanity("Ты перегибаешь, будь полезнее")
    assert signal.social_repair is True
    assert signal.response_intent == "SHORT"


def test_topic_retirement_is_detected_without_group_specific_vocabulary() -> None:
    signal = classify_group_humanity("Хватит эту тему мусолить, давай что-то новенькое")
    assert signal.retire_topic is True


def test_emoji_only_turn_prefers_silence() -> None:
    signal = classify_group_humanity("😂😂")
    assert signal.response_intent == "SILENCE"


def test_tiny_ack_prefers_micro_reply() -> None:
    signal = classify_group_humanity("Красава")
    assert signal.response_intent == "MICRO"


def test_normal_help_request_stays_normal() -> None:
    signal = classify_group_humanity("Подбери три варианта и объясни различия")
    assert signal.social_repair is False
    assert signal.retire_topic is False
    assert signal.response_intent == "NORMAL"
