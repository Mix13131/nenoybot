from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_v2.domain.enums import MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.repositories.maintenance_repo import DuplicateMemoryGroup, ScopeMemoryCount
from app_v2.services.maintenance import MaintenancePolicy, MaintenanceService


NOW = datetime(2026, 9, 14, 8, 30, tzinfo=timezone.utc)


def card(
    memory_id: str,
    *,
    pinned: bool = False,
    evidence_text: str = "evidence",
    source_count: int = 1,
    freshness: float = 0.8,
    confidence: float = 0.9,
) -> MemoryCard:
    return MemoryCard(
        id=memory_id,
        scope_type=ScopeType.PERSONAL,
        scope_id="u1",
        memory_type="observation",
        subject_keys=["user:u1"],
        summary="Один и тот же факт",
        payload={},
        importance=0.8,
        confidence=confidence,
        freshness=freshness,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.EXPLICIT,
        pinned=pinned,
        usage_policy=UsagePolicy(),
        evidence=[
            MemoryEvidence(
                message_id=memory_id,
                author_id="u1",
                timestamp=NOW - timedelta(days=10),
                excerpt=evidence_text,
            )
        ],
        source_count=source_count,
        created_at=NOW - timedelta(days=20),
        updated_at=NOW - timedelta(days=5),
    )


class FakeMaintenanceRepo:
    def __init__(self, duplicate_groups=(), scope_counts=()):
        self._duplicates = list(duplicate_groups)
        self._scope_counts = list(scope_counts)
        self.calls = []

    def expire_raw_message_text(self, **kwargs):
        self.calls.append(("expire", kwargs))
        return 2

    def decay_memory_freshness(self, **kwargs):
        self.calls.append(("decay", kwargs))
        return 3

    def duplicate_memory_groups(self, **kwargs):
        self.calls.append(("duplicates", kwargs))
        return list(self._duplicates)

    def archive_stale_running_jokes(self, **kwargs):
        self.calls.append(("jokes", kwargs))
        return 1

    def active_scope_counts(self):
        return list(self._scope_counts)

    def archive_low_quality_active(self, **kwargs):
        self.calls.append(("cap_archive", kwargs))
        return min(1, kwargs["max_rows"])

    def prune_terminal_events(self, **kwargs):
        self.calls.append(("prune_events", kwargs))
        return 4

    def prune_terminal_outbox(self, **kwargs):
        self.calls.append(("prune_outbox", kwargs))
        return 5


class FakeMemoryRepo:
    def __init__(self, cards):
        self.cards = {item.id: item for item in cards}
        self.updated = []
        self.status_changes = []

    def get(self, scope_type, scope_id, memory_id):
        item = self.cards.get(memory_id)
        if item and item.scope_type is scope_type and item.scope_id == scope_id:
            return item
        return None

    def update(self, item):
        self.cards[item.id] = item
        self.updated.append(item)
        return item

    def set_status(self, scope_type, scope_id, memory_id, status, *, superseded_by=None):
        item = self.get(scope_type, scope_id, memory_id)
        if item is None:
            return False
        self.cards[memory_id] = item.model_copy(
            update={"status": status, "superseded_by": superseded_by, "updated_at": NOW}
        )
        self.status_changes.append((memory_id, status, superseded_by))
        return True


class FakeLeaseRepo:
    def __init__(self, recovered):
        self.recovered = recovered

    def recover_stale(self):
        return self.recovered


def service(maintenance_repo, memory_repo):
    return MaintenanceService(
        maintenance_repo=maintenance_repo,
        memory_repo=memory_repo,
        event_repo=FakeLeaseRepo(6),
        outbox_repo=FakeLeaseRepo(7),
        policy=MaintenancePolicy(),
    )


def test_maintenance_compacts_duplicates_and_preserves_evidence():
    winner = card("m1", evidence_text="first", source_count=1)
    loser = card("m2", evidence_text="second", source_count=2)
    maintenance_repo = FakeMaintenanceRepo(
        duplicate_groups=[
            DuplicateMemoryGroup(
                scope_type=ScopeType.PERSONAL,
                scope_id="u1",
                memory_type="observation",
                memory_ids=("m1", "m2"),
            )
        ]
    )
    memory_repo = FakeMemoryRepo([winner, loser])

    report = service(maintenance_repo, memory_repo).run(now=NOW)

    merged = memory_repo.cards["m1"]
    assert {item.excerpt for item in merged.evidence} == {"first", "second"}
    assert merged.source_count >= 2
    assert "m2" in merged.payload["maintenance"]["compacted_from"]
    assert memory_repo.cards["m2"].status is MemoryStatus.SUPERSEDED
    assert memory_repo.cards["m2"].superseded_by == "m1"
    assert report.duplicate_groups_compacted == 1
    assert report.memories_superseded == 1


def test_pinned_duplicate_is_never_superseded_by_maintenance():
    winner = card("m1", pinned=True)
    pinned_loser = card("m2", pinned=True)
    maintenance_repo = FakeMaintenanceRepo(
        duplicate_groups=[
            DuplicateMemoryGroup(ScopeType.PERSONAL, "u1", "observation", ("m1", "m2"))
        ]
    )
    memory_repo = FakeMemoryRepo([winner, pinned_loser])

    report = service(maintenance_repo, memory_repo).run(now=NOW)

    assert memory_repo.status_changes == []
    assert memory_repo.cards["m2"].status is MemoryStatus.ACTIVE
    assert report.memories_superseded == 0


def test_soft_cap_uses_targeted_archive_and_reports_unresolved_excess():
    maintenance_repo = FakeMaintenanceRepo(
        scope_counts=[ScopeMemoryCount(ScopeType.PERSONAL, "u1", 152)]
    )
    memory_repo = FakeMemoryRepo([])

    report = service(maintenance_repo, memory_repo).run(now=NOW)

    cap_call = next(kwargs for name, kwargs in maintenance_repo.calls if name == "cap_archive")
    assert cap_call["scope_type"] is ScopeType.PERSONAL
    assert cap_call["max_rows"] == 2
    assert report.soft_cap_scopes_triggered == 1
    assert report.soft_cap_memories_archived == 1
    assert report.soft_cap_unresolved_excess == 1


def test_maintenance_runs_retention_recovery_and_pruning_with_defaults():
    maintenance_repo = FakeMaintenanceRepo()
    memory_repo = FakeMemoryRepo([])

    report = service(maintenance_repo, memory_repo).run(now=NOW)

    expire_call = next(kwargs for name, kwargs in maintenance_repo.calls if name == "expire")
    assert expire_call["retention_days"] == 30
    assert report.raw_messages_expired == 2
    assert report.memory_freshness_decayed == 3
    assert report.stale_running_jokes_archived == 1
    assert report.stale_events_recovered == 6
    assert report.stale_outbox_recovered == 7
    assert report.terminal_events_pruned == 4
    assert report.terminal_outbox_pruned == 5


def test_maintenance_requires_timezone_aware_now():
    maintenance_repo = FakeMaintenanceRepo()
    memory_repo = FakeMemoryRepo([])
    try:
        service(maintenance_repo, memory_repo).run(now=datetime(2026, 9, 14, 8, 30))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("expected timezone validation")
