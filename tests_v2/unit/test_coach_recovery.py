"""Invalid operator recovery parameters must not reach a database."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app_v2.services.coach_recovery import CoachRecoveryError, recover_completed_direct


@pytest.mark.parametrize("change", [
    {"since": datetime(2026, 1, 1)}, {"until": datetime(2026, 1, 1)},
    {"now": datetime(2026, 1, 1)}, {"expected_chat_id": True},
    {"expected_chat_id": 1}, {"expected_chat_id": -(2**63)}, {"dry_run": "false"},
    {"since": datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)},
    {"until": datetime(2026, 1, 1, 2, tzinfo=timezone.utc)},
])
def test_invalid_window_or_parameters_fail_before_opening_connection(change):
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    args = dict(since=at, until=at+timedelta(minutes=1), expected_chat_id=-1009000,
                dry_run=True, now=at+timedelta(minutes=2))
    args.update(change)
    def forbidden():
        pytest.fail("invalid recovery must not open a connection")
    with pytest.raises(CoachRecoveryError):
        recover_completed_direct(SimpleNamespace(), connection_factory=forbidden, **args)
