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
        mode=None,
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


def test_delivered_help_mode_wins_over_secondary_callback_reason() -> None:
    assert classify_intervention_family(
        primary_action="reply",
        mode="group_help",
        reason_codes=["help_opportunity", "callback_opportunity"],
        metadata={},
    ) == "direct"


def test_all_direct_group_modes_ignore_secondary_style_opportunities() -> None:
    for mode in ("group_direct_reply", "group_organizer", "group_arbiter"):
        assert classify_intervention_family(
            primary_action="reply",
            mode=mode,
            reason_codes=["callback_opportunity", "roast_opportunity"],
            metadata={},
        ) == "direct"
