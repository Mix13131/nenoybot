from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from app_v2.services.analytics_report import AnalyticsReportService, render_text


START=datetime(2026,9,13,10,0,tzinfo=timezone.utc)
END=datetime(2026,9,14,10,0,tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self): self.calls=[]
    def personal_counts(self,start,end,scope_id):
        self.calls.append(("personal",start,end,scope_id))
        return {
            "messages":12,"replies":10,"proactive_replies":2,"memory_writes":3,
            "grounded_callbacks":4,"llm_cost_usd":0.123456,
        }
    def group_counts(self,start,end,scope_id):
        self.calls.append(("group",start,end,scope_id))
        return {
            "participant_count":8,"organic_participants":3,"direct_mentions":6,
            "unsolicited_interventions":2,"replies":10,"reactions":5,"reaction_rate":.5,
            "roast_interventions":4,"roast_positive":3,"roast_hit_rate":.75,
            "ignored_interventions":1,"negative_feedback":2,"mute_events":1,
            "llm_cost_usd":.456,
        }
    def group_cost_by_day(self,start,end,scope_id):
        self.calls.append(("group_daily",start,end,scope_id))
        return [{"day":START,"scope_id":"-1001","llm_cost_usd":.22}]


def test_personal_report_is_serializable_and_scope_aware() -> None:
    repo=FakeRepo()
    report=AnalyticsReportService(repo).build(scope="personal",start=START,end=END,scope_id="123")
    assert report.metrics["messages"] == 12
    assert report.metrics["grounded_callbacks"] == 4
    assert repo.calls == [("personal",START,END,"123")]
    encoded=json.dumps(report.as_dict())
    assert '"scope": "personal"' in encoded
    assert report.as_dict()["start"] == START.isoformat()


def test_group_report_contains_product_and_cost_metrics() -> None:
    repo=FakeRepo()
    report=AnalyticsReportService(repo).build(scope="group",start=START,end=END,scope_id="-1001")
    assert report.metrics["organic_participants"] == 3
    assert report.metrics["reaction_rate"] == .5
    assert report.metrics["roast_hit_rate"] == .75
    assert report.group_cost_by_day[0]["llm_cost_usd"] == .22
    assert repo.calls[0] == ("group",START,END,"-1001")
    assert repo.calls[1] == ("group_daily",START,END,"-1001")


def test_text_report_is_readable() -> None:
    report=AnalyticsReportService(FakeRepo()).build(scope="group",start=START,end=END,scope_id="-1001")
    text=render_text(report)
    assert "НеНой v2 analytics — group" in text
    assert "organic_participants: 3" in text
    assert "reaction_rate: 0.500" in text
    assert "llm_cost_usd: 0.456000" in text
    assert "group_cost_by_day:" in text


def test_last_hours_builds_timezone_aware_window() -> None:
    repo=FakeRepo()
    report=AnalyticsReportService(repo).last_hours(scope="personal",hours=6,now=END)
    assert report.end == END
    assert (report.end-report.start).total_seconds() == 21600


def test_invalid_windows_are_rejected() -> None:
    service=AnalyticsReportService(FakeRepo())
    with pytest.raises(ValueError,match="start must be before end"):
        service.build(scope="personal",start=END,end=START)
    with pytest.raises(ValueError,match="timezone-aware"):
        service.build(scope="personal",start=START.replace(tzinfo=None),end=END)
    with pytest.raises(ValueError,match="hours"):
        service.last_hours(scope="group",hours=0,now=END)
