"""Operator telemetry does not expose messages/credentials or stop the worker."""
import json

import pytest

from app_v2.coach_admin import main
from app_v2.coach_config import CoachConfig
from app_v2.coach_worker import _print_statistics


def test_periodic_statistics_failure_is_sanitized_and_does_not_escape(monkeypatch, capsys):
    def fail(*a, **kw):
        raise RuntimeError("postgresql://secret should not enter logs")
    monkeypatch.setattr("app_v2.coach_admin.inspect_statistics", fail)
    _print_statistics(CoachConfig())
    assert json.loads(capsys.readouterr().out) == {
        "event": "coach_statistics_unavailable", "error_type": "RuntimeError",
    }


def test_periodic_statistics_selects_current_utc_day(monkeypatch, capsys):
    boundaries = []
    def report(config, *, since):
        boundaries.append(since)
        return {"usage": {"operations": 12}, "configured_budget_mode": "observe"}
    monkeypatch.setattr("app_v2.coach_admin.inspect_statistics", report)
    _print_statistics(CoachConfig())
    at = boundaries[0]
    assert at.hour == at.minute == at.second == at.microsecond == 0
    assert at.utcoffset().total_seconds() == 0
    assert json.loads(capsys.readouterr().out)["usage"]["operations"] == 12


@pytest.mark.parametrize("argv", [
    ["stats", "--since", "2026-01-01"],
    ["stats", "--until", "not-a-date"],
    ["prepare", "--since", "2026-01-01T00:00:00Z"],
])
def test_invalid_statistics_intervals_fail_before_loading_configuration(monkeypatch, argv):
    def unexpected():
        raise AssertionError("CLI arguments must be checked before configuration")
    monkeypatch.setattr("app_v2.coach_admin.load_coach_config", unexpected)
    with pytest.raises(SystemExit) as result:
        main(argv)
    assert result.value.code == 2
