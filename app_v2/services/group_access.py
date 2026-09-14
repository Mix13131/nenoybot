from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app_v2.domain.enums import ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.group_context_repo import GroupContext, GroupContextRepository


@dataclass(frozen=True)
class GroupAccessResult:
    allowed: bool
    reason: str
    context: GroupContext | None = None


class GroupAccessService:
    def __init__(self, repo: GroupContextRepository) -> None:
        self.repo = repo

    def evaluate(self, event: EventEnvelope, *, now: datetime | None = None) -> GroupAccessResult:
        if event.scope_type is not ScopeType.GROUP:
            return GroupAccessResult(False, "not_group_scope")

        context = self.repo.load(event.scope_id, event.actor_user_id)
        if context is None:
            return GroupAccessResult(False, "unknown_group")
        if not context.is_active:
            return GroupAccessResult(False, "group_inactive", context)
        if not context.is_whitelisted:
            return GroupAccessResult(False, "group_not_whitelisted", context)

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        # silent_until does not make the group unapproved. The Dispatcher uses it
        # as a behavior state so direct mentions can still be handled by policy.
        return GroupAccessResult(True, "allowed", context)
