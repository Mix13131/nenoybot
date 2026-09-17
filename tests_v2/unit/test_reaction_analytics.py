from __future__ import annotations

from datetime import datetime, timezone

from app_v2.repositories.analytics_repo import AnalyticsRepository
from app_v2.services.analytics_report import AnalyticsReportService, render_text


START = datetime(2026, 9, 15, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, tzinfo=timezone.utc)


class RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class ModeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return RowsResult(
            [
                ("group_roast", 10, 8, 5, 6, 5, 1, 4, 1, 3, 2, 1, 2),
                ("group_help", 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            ]
        )


def test_reaction_quality_by_mode_is_zero_safe_and_current_state_based() -> None:
    conn = ModeConn()
    rows = AnalyticsRepository(conn).reaction_quality_by_mode(
        START,
        END,
        "-100777",
    )
    roast = rows[0]
    assert roast["mode"] == "group_roast"
    assert roast["interventions"] == 10
    assert roast["reacting_states"] == 8
    assert roast["reacting_participants"] == 5
    assert roast["reacted_interventions"] == 6
    assert roast["positive_votes"] == 5
    assert roast["negative_votes"] == 1
    assert roast["reacted_intervention_rate"] == 0.6
    assert roast["positive_share"] == 5 / 6
    assert roast["laughter_entertainment_states"] == 3
    assert roast["approval_affection_states"] == 2
    assert roast["explicit_negative_states"] == 1
    assert roast["contextual_unknown_states"] == 2

    help_mode = rows[1]
    assert help_mode["reacted_intervention_rate"] == 0.0
    assert help_mode["positive_share"] == 0.0

    sql, params = conn.calls[0]
    normalized = " ".join(sql.split())
    assert "PARTITION BY f.scope_id, f.intervention_id, f.user_id" in normalized
    assert "payload ->> 'source_event_id'" in normalized
    assert "split_part(f.payload ->> 'source_event_id', ':', 2)::bigint" in normalized
    assert "DESC NULLS LAST" in normalized
    assert "f.created_at DESC" in normalized
    assert "f.id DESC" in normalized
    assert "f.user_id IS NOT NULL" in normalized
    assert "feedback_type <> 'reaction_removed'" in normalized
    assert "reaction_families" in normalized
    assert params == (START, END, "-100777", END)


class ReportRepo:
    def group_counts(self, start, end, scope_id):
        return {
            "participant_count": 3,
            "organic_participants": 2,
            "direct_mentions": 2,
            "unsolicited_interventions": 1,
            "replies": 4,
            "reactions": 2,
            "reaction_rate": 0.5,
            "roast_interventions": 2,
            "roast_positive": 1,
            "roast_hit_rate": 0.5,
            "ignored_interventions": 0,
            "negative_feedback": 0,
            "mute_events": 0,
            "llm_cost_usd": 0.1,
        }

    def group_cost_by_day(self, start, end, scope_id):
        return []

    def reaction_quality_by_mode(self, start, end, scope_id):
        return [
            {
                "mode": "group_roast",
                "interventions": 2,
                "reacting_states": 2,
                "reacting_participants": 2,
                "reacted_interventions": 2,
                "positive_votes": 2,
                "negative_votes": 0,
                "interventions_with_positive": 2,
                "interventions_with_negative": 0,
                "reacted_intervention_rate": 1.0,
                "positive_share": 1.0,
                "laughter_entertainment_states": 2,
                "approval_affection_states": 0,
                "explicit_negative_states": 0,
                "contextual_unknown_states": 0,
            }
        ]


def test_group_report_exposes_reaction_quality_without_adapting_behavior() -> None:
    report = AnalyticsReportService(ReportRepo()).build(
        scope="group",
        start=START,
        end=END,
        scope_id="-100777",
    )
    data = report.as_dict()
    assert data["reaction_quality_by_mode"][0]["mode"] == "group_roast"
    assert data["reaction_quality_by_mode"][0]["laughter_entertainment_states"] == 2
    text = render_text(report)
    assert "reaction_quality_by_mode:" in text
    assert "group_roast:" in text
    assert "laughter=2" in text
