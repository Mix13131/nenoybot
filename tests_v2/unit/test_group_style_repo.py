from app_v2.repositories.group_style_repo import classify_intervention_family


def test_family_classification_is_deterministic_and_bounded() -> None:
    assert classify_intervention_family(
        primary_action="reaction_only",
        mode=None,
        reason_codes=[],
        metadata={},
    ) == "direct"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_direct_reply",
        reason_codes=["direct_mention"],
        metadata={"feedback_family": "social_ack"},
    ) == "social_ack"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_roast",
        reason_codes=["roast_opportunity"],
        metadata={},
    ) == "banter_roast"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_callback",
        reason_codes=["callback_opportunity"],
        metadata={},
    ) == "callback"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_direct_reply",
        reason_codes=["direct_mention"],
        metadata={},
    ) == "direct"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_banter",
        reason_codes=["scheduled_reminder"],
        metadata={},
    ) == "direct"
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_direct_reply",
        reason_codes=[],
        metadata={},
    ) == "proactive"


def test_delivered_callback_mode_wins_over_secondary_roast_reason() -> None:
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_callback",
        reason_codes=["callback_opportunity", "roast_opportunity"],
        metadata={},
    ) == "callback"
