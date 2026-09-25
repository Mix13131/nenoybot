from __future__ import annotations

from datetime import datetime, timezone

from app_v2.repositories.analytics_repo import AnalyticsRepository


START=datetime(2026,9,13,tzinfo=timezone.utc)
END=datetime(2026,9,14,tzinfo=timezone.utc)


class Result:
    def __init__(self, row=None, rows=None): self.row=row; self.rows=rows or []
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class FakeConn:
    def __init__(self, scalars):
        self.scalars=list(scalars); self.calls=[]
    def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if "GROUP BY day" in sql:
            return Result(rows=[(START,"-1001",.25)])
        value=self.scalars.pop(0)
        return Result(row=value if isinstance(value, tuple) else (value,))


def test_personal_queries_are_scope_filtered() -> None:
    conn=FakeConn([
        12,10,2,3,4,.123,
        (5,4,1,2,1,12000),
        (7000,.045),
    ])
    metrics=AnalyticsRepository(conn).personal_counts(START,END,"123")
    assert metrics == {
        "messages":12,"replies":10,"proactive_replies":2,"memory_writes":3,
        "grounded_callbacks":4,"llm_cost_usd":.123,
        "url_read_requests":5,
        "url_read_successes":4,
        "url_read_failures":1,
        "url_read_success_rate":.8,
        "url_read_cache_hits":2,
        "url_read_firecrawl_reads":1,
        "url_read_content_chars":12000,
        "url_read_avg_content_chars":3000.0,
        "url_read_generator_input_tokens":7000,
        "url_read_generator_cost_usd":.045,
    }
    assert all("scope_id=%s" in sql for sql,_ in conn.calls)
    assert all(params[-1] == "123" for _,params in conn.calls)


def test_group_ratios_are_zero_safe() -> None:
    # participant, organic, direct, unsolicited, replies, silence_wakeups,
    # reactions, roast_total, roast_positive, ignored, negative, mute, cost
    conn=FakeConn([
        8,3,6,2,0,1,5,0,3,1,2,1,.45,
        (2,1,1,0,0,4500),
        (3000,.02),
    ])
    metrics=AnalyticsRepository(conn).group_counts(START,END,"-1001")
    assert metrics["participant_count"] == 8
    assert metrics["silence_wakeups"] == 1
    assert metrics["reaction_rate"] == 0.0
    assert metrics["roast_hit_rate"] == 0.0
    assert metrics["negative_feedback"] == 2
    assert metrics["llm_cost_usd"] == .45
    assert metrics["url_read_requests"] == 2
    assert metrics["url_read_successes"] == 1
    assert metrics["url_read_failures"] == 1
    assert metrics["url_read_success_rate"] == .5
    assert metrics["url_read_content_chars"] == 4500
    assert metrics["url_read_generator_input_tokens"] == 3000
    assert metrics["url_read_generator_cost_usd"] == .02


def test_group_cost_by_day_serializes_float_cost() -> None:
    conn=FakeConn([])
    rows=AnalyticsRepository(conn).group_cost_by_day(START,END,"-1001")
    assert rows == [{"day":START,"scope_id":"-1001","llm_cost_usd":.25}]
    sql,params=conn.calls[0]
    assert "e.scope_type='group'" in sql
    assert "e.scope_id=%s" in sql
    assert params[-1] == "-1001"
