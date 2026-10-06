from __future__ import annotations

import json

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.repositories.intervention_repo import InterventionRepository


class Result:
    def fetchone(self):
        return (42,)


class FakeConn:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Result()

    def commit(self):
        self.commits += 1


def test_intervention_persists_only_sanitized_capability_telemetry() -> None:
    conn = FakeConn()
    decision = DispatcherDecision(
        primary_action=PrimaryAction.REPLY,
        mode=ResponseMode.ASSISTANT,
        metadata={
            "policy_version": "test-policy",
            "decision_private_context": "must-not-persist",
        },
    )

    record = InterventionRepository(conn).record(
        event_id="evt-1",
        scope_type=ScopeType.PERSONAL,
        scope_id="u1",
        decision=decision,
        selected_memory_ids=[],
        generated_text="ok",
        extra_metadata={
            "statement_watch": {
                "evidence_excerpt": "private historical text",
            },
            "url_read": {
                "status": "succeeded",
                "host": "example.com",
                "source": "direct",
                "cache_hit": False,
                "content_chars": 1234,
            },
            "feedback_family": "social_ack",
        },
    )

    assert record.id == 42
    assert conn.commits == 1
    sql, params = conn.calls[0]
    assert "metadata" in sql
    persisted = json.loads(params[-1])
    assert persisted == {
        "url_read": {
            "status": "succeeded",
            "host": "example.com",
            "source": "direct",
            "cache_hit": False,
            "content_chars": 1234,
        },
        "feedback_family": "social_ack",
    }
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert "must-not-persist" not in serialized
    assert "private historical text" not in serialized


def test_initiative_opportunity_metadata_is_strictly_sanitized() -> None:
    conn = FakeConn()
    decision = DispatcherDecision(
        primary_action=PrimaryAction.IGNORE,
        metadata={
            "policy_version": "test-policy",
            "initiative_opportunity": {
                "eligible": False,
                "quality_score": 999,
                "hook_family": "invented-private-hook",
                "reasons": [
                    "grounded_callback",
                    "raw text from user",
                ],
                "suppressors": [
                    "no_grounded_hook",
                    "user:123456",
                ],
                "stale_context": True,
                "version": "attacker-version",
                "raw_text": "private chat text",
                "actor_id": "123456",
            },
        },
    )

    InterventionRepository(conn).record(
        event_id="evt-initiative",
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        decision=decision,
        selected_memory_ids=[],
        generated_text=None,
    )

    _, params = conn.calls[0]
    persisted = json.loads(params[-1])
    opportunity = persisted["initiative_opportunity"]

    assert opportunity == {
        "eligible": False,
        "quality_score": 100,
        "hook_family": None,
        "reasons": ["grounded_callback"],
        "suppressors": ["no_grounded_hook"],
        "stale_context": True,
        "version": "initiative-opportunity-v1",
    }
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert "private chat text" not in serialized
    assert "123456" not in serialized
    assert "raw text from user" not in serialized
