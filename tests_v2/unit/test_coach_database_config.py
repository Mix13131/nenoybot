"""Credential-preserving target DB selection; parsing only, no connections."""
from urllib.parse import quote

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app_v2.coach_config import CoachConfigurationError, load_coach_config


@pytest.mark.parametrize('password', [
    'synthetic:p@ss/word?x=1&y=%two#', "synthetic 'quote' \\ slash and spaces", 'synthetic+%=:@/?#',
])
def test_base_url_override_preserves_encoded_password_and_all_other_connection_options(password, monkeypatch):
    base = ('postgresql://synthetic:' + quote(password, safe='') +
            '@db.synthetic.invalid:5544/provider_db?sslmode=require&application_name=synthetic%20arni'
            '&options=-c%20statement_timeout%3D5000')
    def no_connection(*args, **kwargs):
        raise AssertionError('Loading configuration must never connect to PostgreSQL')
    monkeypatch.setattr(psycopg, 'connect', no_connection)
    config = load_coach_config({'COACH_DATABASE_BASE_URL': base, 'COACH_DATABASE_NAME': 'coach_synthetic'})
    original, resolved = conninfo_to_dict(base), conninfo_to_dict(config.database_url)
    assert resolved == {**original, 'dbname': 'coach_synthetic'}
    assert resolved['password'] == password
    assert password not in repr(config) and base not in repr(config)
    assert not hasattr(config, 'database_base_url')


def test_direct_database_url_remains_unchanged():
    direct = 'postgresql://synthetic:p%40ss%2Fword@db.synthetic.invalid/existing?sslmode=require'
    assert load_coach_config({'COACH_DATABASE_URL': direct}).database_url == direct


@pytest.mark.parametrize('extra', [
    {'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base'},
    {'COACH_DATABASE_NAME': 'coach_synthetic'},
    {'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base', 'COACH_DATABASE_NAME': 'coach_synthetic'},
])
def test_direct_database_url_cannot_be_combined_with_a_partial_or_complete_override(extra):
    with pytest.raises(CoachConfigurationError, match='cannot be combined') as error:
        load_coach_config({'COACH_DATABASE_URL': 'postgresql://synthetic:PRIVATE_SECRET@host/direct', **extra})
    assert 'PRIVATE_SECRET' not in str(error.value) and 'postgresql://' not in str(error.value)


@pytest.mark.parametrize('partial', [
    {'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base'},
    {'COACH_DATABASE_NAME': 'coach_synthetic'},
    {'COACH_DATABASE_BASE_URL': '   ', 'COACH_DATABASE_NAME': 'coach_synthetic'},
    {'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base', 'COACH_DATABASE_NAME': ' '},
])
def test_database_override_requires_both_explicit_values_without_environment_fallback(partial):
    with pytest.raises(CoachConfigurationError, match='must be provided together'):
        load_coach_config({'DATABASE_URL': 'postgresql://legacy/never-use',
                           'NENOY_V2_DATABASE_URL': 'postgresql://legacy/never-use', **partial})


@pytest.mark.parametrize('name', [
    'postgres', 'coach_', 'coach_UPPER', 'coach_имя', 'coach_bad-name', 'coach_x;DROP DATABASE postgres',
    'coach_x\nother', 'coach_' + 'x' * 58,
])
def test_database_override_rejects_ambiguous_or_unsafe_names(name):
    with pytest.raises(CoachConfigurationError, match='COACH_DATABASE_NAME'):
        load_coach_config({'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base',
                           'COACH_DATABASE_NAME': name})


def test_database_name_accepts_the_explicit_maximum_postgres_identifier_length():
    name = 'coach_' + 'x' * 57
    config = load_coach_config({'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/base',
                                'COACH_DATABASE_NAME': name})
    assert len(name) == 63 and conninfo_to_dict(config.database_url)['dbname'] == name


def test_invalid_base_connection_error_never_echoes_credentials_or_uri():
    base = 'postgresql://synthetic:PRIVATE_CREDENTIAL@host/base?unknown_conninfo_option=PRIVATE_VALUE'
    with pytest.raises(CoachConfigurationError, match='invalid PostgreSQL connection information') as error:
        load_coach_config({'COACH_DATABASE_BASE_URL': base, 'COACH_DATABASE_NAME': 'coach_synthetic'})
    assert 'PRIVATE_' not in str(error.value) and base not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_explicit_database_pair_satisfies_automatic_runtime_activation():
    values = {
        'COACH_ENV': 'production', 'COACH_GROUP_MODE': 'joined_groups', 'COACH_AI_ENABLED': '0',
        'COACH_DATABASE_BASE_URL': 'postgresql://synthetic:secret@host/provider_db',
        'COACH_DATABASE_NAME': 'coach_synthetic', 'COACH_BOT_USERNAME': 'synthetic_bot',
        'COACH_TELEGRAM_BOT_TOKEN': '900001:' + 's' * 30, 'COACH_WEBHOOK_SECRET': 'w' * 32,
    }
    config = load_coach_config(values)
    assert config.enabled and not config.ai_enabled
    assert conninfo_to_dict(config.database_url)['dbname'] == 'coach_synthetic'
