"""Operator recovery is explicit, bounded and non-writing unless --apply is used."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app_v2 import coach_admin
from app_v2.services.coach_recovery import CoachRecoveryError


ARGS = ['recover-direct', '--since', '2026-01-02T10:00:00Z',
        '--until', '2026-01-02T10:01:00Z', '--chat-id', '-100990000001']


@pytest.mark.parametrize('apply', [False, True])
def test_cli_requires_explicit_apply_and_preserves_recovery_bounds(monkeypatch, capsys, apply):
    config = SimpleNamespace()
    calls = []
    monkeypatch.setattr(coach_admin, 'load_coach_config', lambda: config)
    def recover(actual_config, **kwargs):
        assert actual_config is config
        calls.append(kwargs)
        return dict(status='rearmed' if apply else 'preview', dry_run=not apply,
                    reason='operator_recovery_rearmed' if apply else 'eligible_completed_direct',
                    counts=dict(blocked=0, rearmed=int(apply)))
    monkeypatch.setattr(coach_admin, 'recover_completed_direct', recover)
    assert coach_admin.main(ARGS + (['--apply'] if apply else [])) == 0
    assert calls == [dict(since=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
                          until=datetime(2026, 1, 2, 10, 1, tzinfo=timezone.utc),
                          expected_chat_id=-100990000001, dry_run=not apply)]
    assert json.loads(capsys.readouterr().out)['dry_run'] is not apply


@pytest.mark.parametrize('args', [
    ['recover-direct'],
    ['recover-direct', '--since', '2026-01-02T10:00:00Z'],
    ARGS + ['--file', 'must-not-be-opened.json'],
    ['stats', '--apply'],
    ['prepare', '--since', '2026-01-02T10:00:00Z'],
])
def test_invalid_recovery_cli_arguments_fail_before_configuration(monkeypatch, args):
    monkeypatch.setattr(coach_admin, 'load_coach_config', lambda: pytest.fail('must not load configuration'))
    with pytest.raises(SystemExit) as exc:
        coach_admin.main(args)
    assert exc.value.code == 2


def test_blocked_recovery_returns_failure_without_hidden_retry(monkeypatch, capsys):
    monkeypatch.setattr(coach_admin, 'load_coach_config', lambda: SimpleNamespace())
    calls = []
    def recover(*args, **kwargs):
        calls.append(kwargs)
        return dict(status='unchanged', dry_run=False, reason='output_already_exists', counts={'blocked': 1})
    monkeypatch.setattr(coach_admin, 'recover_completed_direct', recover)
    assert coach_admin.main(ARGS + ['--apply']) == 1
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)['reason'] == 'output_already_exists'


def test_recovery_validation_error_is_a_machine_reason(monkeypatch, capsys):
    monkeypatch.setattr(coach_admin, 'load_coach_config', lambda: SimpleNamespace())
    def recover(*args, **kwargs):
        raise CoachRecoveryError('invalid_recovery_window')
    monkeypatch.setattr(coach_admin, 'recover_completed_direct', recover)
    assert coach_admin.main(ARGS) == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'blocked', 'reason': 'invalid_recovery_window'}
