from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal


@dataclass(frozen=True)
class AnalyticsReport:
    scope: Literal["personal", "group"]
    scope_id: str | None
    start: datetime
    end: datetime
    metrics: dict[str, int | float]
    group_cost_by_day: tuple[dict[str, Any], ...] = ()
    reaction_quality_by_mode: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        data["group_cost_by_day"] = [
            {
                **item,
                "day": item["day"].isoformat() if hasattr(item.get("day"), "isoformat") else item.get("day"),
            }
            for item in self.group_cost_by_day
        ]
        data["reaction_quality_by_mode"] = [
            dict(item) for item in self.reaction_quality_by_mode
        ]
        return data


class AnalyticsReportService:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    def build(
        self,
        *,
        scope: Literal["personal", "group"],
        start: datetime,
        end: datetime,
        scope_id: str | None = None,
    ) -> AnalyticsReport:
        self._validate_window(start, end)
        if scope == "personal":
            metrics = self.repo.personal_counts(start, end, scope_id)
            return AnalyticsReport(scope, scope_id, start, end, metrics)
        if scope == "group":
            metrics = self.repo.group_counts(start, end, scope_id)
            daily = tuple(self.repo.group_cost_by_day(start, end, scope_id))
            quality_reader = getattr(self.repo, "reaction_quality_by_mode", None)
            quality = (
                tuple(quality_reader(start, end, scope_id))
                if callable(quality_reader)
                else ()
            )
            return AnalyticsReport(
                scope,
                scope_id,
                start,
                end,
                metrics,
                daily,
                quality,
            )
        raise ValueError(f"Unsupported analytics scope: {scope}")

    def last_hours(
        self,
        *,
        scope: Literal["personal", "group"],
        hours: int = 24,
        scope_id: str | None = None,
        now: datetime | None = None,
    ) -> AnalyticsReport:
        if hours <= 0:
            raise ValueError("hours must be > 0")
        end = now or datetime.now(timezone.utc)
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return self.build(
            scope=scope,
            start=end - timedelta(hours=hours),
            end=end,
            scope_id=scope_id,
        )

    @staticmethod
    def _validate_window(start: datetime, end: datetime) -> None:
        for name, value in (("start", start), ("end", end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")


def render_text(report: AnalyticsReport) -> str:
    lines = [
        f"НеНой v2 analytics — {report.scope}",
        f"window: {report.start.isoformat()} → {report.end.isoformat()}",
    ]
    if report.scope_id is not None:
        lines.append(f"scope_id: {report.scope_id}")
    lines.append("")
    for key, value in report.metrics.items():
        if isinstance(value, float):
            lines.append(f"{key}: {value:.6f}" if "cost" in key else f"{key}: {value:.3f}")
        else:
            lines.append(f"{key}: {value}")
    if report.reaction_quality_by_mode:
        lines.append("")
        lines.append("reaction_quality_by_mode:")
        for item in report.reaction_quality_by_mode:
            lines.append(
                "  "
                + f"{item.get('mode')}: "
                + f"interventions={item.get('interventions', 0)} "
                + f"reacted={item.get('reacted_interventions', 0)} "
                + f"positive={item.get('positive_votes', 0)} "
                + f"negative={item.get('negative_votes', 0)} "
                + f"laughter={item.get('laughter_entertainment_states', 0)} "
                + f"contextual={item.get('contextual_unknown_states', 0)}"
            )
    if report.group_cost_by_day:
        lines.append("")
        lines.append("group_cost_by_day:")
        for item in report.group_cost_by_day:
            day = item["day"].isoformat() if hasattr(item.get("day"), "isoformat") else str(item.get("day"))
            lines.append(f"  {day} {item['scope_id']}: ${float(item['llm_cost_usd']):.6f}")
    return "\n".join(lines)
