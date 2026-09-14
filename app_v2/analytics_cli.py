from __future__ import annotations

import argparse
import json

from app_v2.adapters.postgres import connect
from app_v2.repositories.analytics_repo import AnalyticsRepository
from app_v2.services.analytics_report import AnalyticsReportService, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="НеНой v2 product analytics")
    parser.add_argument("--scope", choices=("personal", "group"), required=True)
    parser.add_argument("--scope-id", default=None)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with connect() as conn:
        report = AnalyticsReportService(AnalyticsRepository(conn)).last_hours(
            scope=args.scope,
            hours=args.hours,
            scope_id=args.scope_id,
        )
    if args.as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
