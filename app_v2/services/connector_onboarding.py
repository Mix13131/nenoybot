from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.services.connector_codec import decode_connector_payload
from app_v2.services.connector_presets import build_connector_preset
from app_v2.services.connector_resolver import connector_id_for_scope


class ConnectorOnboardingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectorOnboardingResult:
    title: str
    preset: str
    created: bool
    version: int
    whitelisted: bool

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "preset": self.preset,
            "created": self.created,
            "version": self.version,
            "whitelisted": self.whitelisted,
        }


class ConnectorOnboardingService:
    """Controlled exact-title onboarding into the persisted connector registry."""

    def __init__(self, *, group_repo: Any, connector_repo: Any) -> None:
        self.group_repo = group_repo
        self.connector_repo = connector_repo

    def apply_exact_title(
        self,
        *,
        title: str,
        preset_name: str,
        created_by: str = "connector_preset_onboarding",
        owner_subjects: tuple[str, ...] = (),
    ) -> ConnectorOnboardingResult:
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise ConnectorOnboardingError("group title is required")
        normalized_preset = str(preset_name or "").strip()
        if not normalized_preset:
            raise ConnectorOnboardingError("preset name is required")

        matches = self.group_repo.find_groups_by_exact_title(normalized_title)
        if not matches:
            raise ConnectorOnboardingError("group title not found")
        if len(matches) != 1:
            raise ConnectorOnboardingError("group title is ambiguous")

        group = matches[0]
        if not group.is_active:
            raise ConnectorOnboardingError("group is not active")

        scope_id = str(group.telegram_chat_id)
        connector_id = connector_id_for_scope("group", scope_id)
        desired = build_connector_preset(
            normalized_preset,
            connector_id=connector_id,
            version=1,
            status="live",
            owner_subjects=owner_subjects,
        )

        existing = self.connector_repo.get_for_scope("group", scope_id)
        created = False
        if existing is None:
            created = self.connector_repo.create(
                scope_type="group",
                scope_id=scope_id,
                config=desired,
                created_by=created_by,
            )
            if not created:
                # Another worker/process may have won the create race. Re-read
                # and verify exact equivalence rather than overwriting.
                existing = self.connector_repo.get_for_scope("group", scope_id)
                if existing is None:
                    raise ConnectorOnboardingError(
                        "connector create lost but persisted record is unavailable"
                    )
        if existing is not None:
            current = decode_connector_payload(
                connector_id=existing.connector_id,
                connector_type=existing.connector_type,
                status=existing.status,
                version=existing.version,
                payload=existing.config,
            )
            if current != desired:
                raise ConnectorOnboardingError(
                    "existing connector differs from requested preset"
                )

        if not group.is_whitelisted:
            if not self.group_repo.set_whitelisted(scope_id, True):
                raise ConnectorOnboardingError("failed to whitelist group")

        return ConnectorOnboardingResult(
            title=normalized_title,
            preset=normalized_preset,
            created=created,
            version=1,
            whitelisted=True,
        )
