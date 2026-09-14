from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app_v2.domain.enums import MemoryStatus, ScopeType
from app_v2.domain.memory import MemoryCard, MemoryEvidence
from app_v2.repositories.maintenance_repo import DuplicateMemoryGroup, MaintenanceRepository


@dataclass(frozen=True)
class MaintenancePolicy:
    warm_retention_days: int = 30
    terminal_retention_days: int = 30
    personal_memory_soft_cap: int = 150
    group_memory_soft_cap: int = 250
    low_quality_unused_days: int = 90
    low_quality_freshness_threshold: float = 0.15
    low_quality_confidence_threshold: float = 0.65
    running_joke_unused_days: int = 30
    running_joke_freshness_threshold: float = 0.25
    duplicate_group_limit: int = 100

    def soft_cap_for(self, scope_type: ScopeType) -> int:
        return (
            self.personal_memory_soft_cap
            if scope_type is ScopeType.PERSONAL
            else self.group_memory_soft_cap
        )


@dataclass(frozen=True)
class MaintenanceReport:
    raw_messages_expired: int = 0
    memory_freshness_decayed: int = 0
    duplicate_groups_compacted: int = 0
    memories_superseded: int = 0
    stale_running_jokes_archived: int = 0
    soft_cap_scopes_triggered: int = 0
    soft_cap_memories_archived: int = 0
    soft_cap_unresolved_excess: int = 0
    stale_events_recovered: int = 0
    stale_outbox_recovered: int = 0
    terminal_events_pruned: int = 0
    terminal_outbox_pruned: int = 0


class MaintenanceService:
    """Run safe, repeatable maintenance over v2 durable state."""

    def __init__(
        self,
        *,
        maintenance_repo: MaintenanceRepository,
        memory_repo,
        event_repo,
        outbox_repo,
        policy: MaintenancePolicy | None = None,
    ) -> None:
        self.maintenance_repo = maintenance_repo
        self.memory_repo = memory_repo
        self.event_repo = event_repo
        self.outbox_repo = outbox_repo
        self.policy = policy or MaintenancePolicy()

    def run(self, *, now: datetime | None = None) -> MaintenanceReport:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        expired = self.maintenance_repo.expire_raw_message_text(
            now=current,
            retention_days=self.policy.warm_retention_days,
        )
        decayed = self.maintenance_repo.decay_memory_freshness(now=current)

        compacted_groups = 0
        superseded = 0
        duplicate_groups = self.maintenance_repo.duplicate_memory_groups(
            limit_groups=self.policy.duplicate_group_limit
        )
        for group in duplicate_groups:
            group_superseded = self._compact_duplicate_group(group, now=current)
            if group_superseded:
                compacted_groups += 1
                superseded += group_superseded

        stale_jokes = self.maintenance_repo.archive_stale_running_jokes(
            now=current,
            unused_days=self.policy.running_joke_unused_days,
            freshness_threshold=self.policy.running_joke_freshness_threshold,
        )

        cap_scopes = 0
        cap_archived = 0
        unresolved = 0
        for item in self.maintenance_repo.active_scope_counts():
            cap = self.policy.soft_cap_for(item.scope_type)
            excess = max(0, item.active_count - cap)
            if excess <= 0:
                continue
            cap_scopes += 1
            archived = self.maintenance_repo.archive_low_quality_active(
                scope_type=item.scope_type,
                scope_id=item.scope_id,
                max_rows=excess,
                now=current,
                unused_days=self.policy.low_quality_unused_days,
                freshness_threshold=self.policy.low_quality_freshness_threshold,
                confidence_threshold=self.policy.low_quality_confidence_threshold,
            )
            cap_archived += archived
            unresolved += max(0, excess - archived)

        stale_events = self.event_repo.recover_stale()
        stale_outbox = self.outbox_repo.recover_stale()
        pruned_events = self.maintenance_repo.prune_terminal_events(
            now=current,
            retention_days=self.policy.terminal_retention_days,
        )
        pruned_outbox = self.maintenance_repo.prune_terminal_outbox(
            now=current,
            retention_days=self.policy.terminal_retention_days,
        )

        return MaintenanceReport(
            raw_messages_expired=expired,
            memory_freshness_decayed=decayed,
            duplicate_groups_compacted=compacted_groups,
            memories_superseded=superseded,
            stale_running_jokes_archived=stale_jokes,
            soft_cap_scopes_triggered=cap_scopes,
            soft_cap_memories_archived=cap_archived,
            soft_cap_unresolved_excess=unresolved,
            stale_events_recovered=stale_events,
            stale_outbox_recovered=stale_outbox,
            terminal_events_pruned=pruned_events,
            terminal_outbox_pruned=pruned_outbox,
        )

    def _compact_duplicate_group(
        self,
        group: DuplicateMemoryGroup,
        *,
        now: datetime,
    ) -> int:
        cards = [
            self.memory_repo.get(group.scope_type, group.scope_id, memory_id)
            for memory_id in group.memory_ids
        ]
        cards = [card for card in cards if card is not None]
        if len(cards) < 2:
            return 0

        # SQL already puts the strongest/pinned/active card first.
        winner = cards[0]
        losers = [card for card in cards[1:] if not card.pinned]
        if not losers:
            return 0

        merged_cards = [winner, *losers]
        merged_evidence = _merge_evidence(card.evidence for card in merged_cards)
        merged_subject_keys = sorted({key for card in merged_cards for key in card.subject_keys})
        source_count = max(
            len(merged_evidence),
            *(int(card.source_count) for card in merged_cards),
        )
        payload = dict(winner.payload)
        maintenance_meta = payload.get("maintenance")
        maintenance = dict(maintenance_meta) if isinstance(maintenance_meta, dict) else {}
        compacted_from = set(str(item) for item in maintenance.get("compacted_from", []) if item)
        compacted_from.update(card.id for card in losers)
        maintenance["compacted_from"] = sorted(compacted_from)
        maintenance["last_compacted_at"] = now.isoformat()
        payload["maintenance"] = maintenance

        updated = winner.model_copy(
            update={
                "subject_keys": merged_subject_keys,
                "evidence": merged_evidence,
                "source_count": source_count,
                "importance": max(float(card.importance) for card in merged_cards),
                "confidence": max(float(card.confidence) for card in merged_cards),
                "freshness": max(float(card.freshness) for card in merged_cards),
                "payload": payload,
                "updated_at": now,
            }
        )
        self.memory_repo.update(updated)

        superseded = 0
        for card in losers:
            changed = self.memory_repo.set_status(
                group.scope_type,
                group.scope_id,
                card.id,
                MemoryStatus.SUPERSEDED,
                superseded_by=winner.id,
            )
            superseded += 1 if changed else 0
        return superseded


def _merge_evidence(groups: Iterable[list[MemoryEvidence]]) -> list[MemoryEvidence]:
    by_key: dict[tuple[str | None, str | None, str, str], MemoryEvidence] = {}
    for evidence_group in groups:
        for item in evidence_group:
            key = (
                item.message_id,
                item.author_id,
                item.timestamp.isoformat(),
                item.excerpt.strip(),
            )
            by_key[key] = item
    return sorted(by_key.values(), key=lambda item: item.timestamp)
