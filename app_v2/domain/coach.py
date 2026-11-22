"""Small contracts for the isolated, individually authorized coach runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceProof:
    source_id: str
    checksum: str
    access_version: int


@dataclass(frozen=True)
class CoachPermissions:
    version: int = 0
    retain_source: bool = False
    historical_inference: bool = False
    auto_report_analysis: bool = False
    unsolicited_group_reply: bool = False
    direct_request_group_reply: bool = False
    track_status: str = "off"
    disclosure_delivered: bool = False

    @property
    def automatic_allowed(self) -> bool:
        return all((self.retain_source, self.historical_inference,
                    self.auto_report_analysis, self.unsolicited_group_reply))


@dataclass(frozen=True)
class CoachJob:
    id: str
    chat_id: int
    message_thread_id: int
    author_user_id: int
    telegram_update_id: int
    telegram_message_id: int
    mode: str
    current_text: str
    source_checksum: str
    source_id: str | None
    consent_version: int
    lease_token: str
    occurred_at: datetime
    window_days: int = 7
    public_confirmed: bool = False


@dataclass(frozen=True)
class CoachOutput:
    id: str
    chat_id: int
    message_thread_id: int
    author_user_id: int
    text: str
    kind: str
    mode: str
    source_proofs: tuple[SourceProof, ...]
    consent_version: int | None
    lease_token: str
    job_id: str | None
    request_message_id: int
    request_checksum: str | None
    source_id: str | None = None
    disclosure: bool = False
    experiment_id: str | None = None


@dataclass(frozen=True)
class ControlResult:
    handled: bool
    reply_text: str | None = None
    job_mode: str | None = None
    disclosure: bool = False
    window_days: int = 7
    public_confirmed: bool = False


GRANT_FIELDS = (
    "retain_source", "historical_inference", "auto_report_analysis",
    "unsolicited_group_reply", "direct_request_group_reply",
)
