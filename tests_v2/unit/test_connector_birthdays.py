from __future__ import annotations

from app_v2.services.connector_codec import decode_connector_payload, encode_connector_payload
from app_v2.services.connector_presets import build_connector_preset


def test_existing_persisted_connector_without_birthday_fields_stays_compatible():
    config=build_connector_preset(
        "education_community_v1",
        connector_id="conn-old",
    )
    payload=encode_connector_payload(config)
    payload["behavior"].pop("birthday_hour", None)
    payload["capabilities"].pop("birthdays", None)

    decoded=decode_connector_payload(
        connector_id=config.connector_id,
        connector_type=config.connector_type,
        status=config.status,
        version=config.version,
        payload=payload,
    )

    assert decoded.behavior.birthday_hour == 9
    assert decoded.capabilities.birthdays is True
    assert "birthdays" in decoded.capabilities.enabled_names()


def test_birthday_capability_round_trips_through_connector_codec():
    config=build_connector_preset(
        "education_community_v1",
        connector_id="conn-birthday",
    )
    decoded=decode_connector_payload(
        connector_id=config.connector_id,
        connector_type=config.connector_type,
        status=config.status,
        version=config.version,
        payload=encode_connector_payload(config),
    )

    assert decoded == config
    assert decoded.behavior.birthday_hour == 9
    assert decoded.capabilities.birthdays is True
