# TASK 03 — PostgreSQL Schema + Migration Runner

## Status

**IN PROGRESS**

## Goal

Create the first isolated НеНой 2.0 PostgreSQL schema and a small versioned SQL migration runner. The implementation must not depend on the legacy v1 schema and must not introduce an ORM or Alembic.

## Base / branch

- Base: `v2`
- Work branch: `task/v2-03-postgres-schema`

## Source of truth

Read before changing code:

- `docs/v2/ARCHITECTURE.md`
- `docs/v2/MEMORY_SPEC.md`
- `docs/v2/DISPATCHER_SPEC.md`
- `docs/v2/MVP_BUILD_PLAN.md`
- `AGENTS.md`

## Allowed scope

- `app_v2/db/**`
- `app_v2/adapters/postgres.py`
- `tests_v2/integration/**`
- `requirements.txt` only if actually necessary
- this task file

Do not modify legacy `app/`, `tests/`, runtime v1, Telegram integration, LLM integration, Dispatcher policy, or product specs.

## Required schema

Create these MVP tables:

- `schema_migrations`
- `users`
- `chats`
- `chat_members`
- `messages`
- `events`
- `memory_cards`
- `memory_relations`
- `tasks`
- `reminders`
- `interventions`
- `feedback_events`
- `outbox`
- `llm_usage`

## Required constraints / indexes

At minimum:

- unique `users.telegram_user_id`;
- unique `chats.telegram_chat_id`;
- unique nullable `events.telegram_update_id`;
- unique `(messages.chat_id, messages.telegram_message_id)`;
- unique `outbox.dedupe_key`;
- memory scope/status indexes;
- pending/retry indexes for `events`, `reminders`, and `outbox`;
- foreign keys for relational rows where appropriate;
- score fields constrained to their documented ranges;
- non-negative attempt/token/count fields;
- timestamps stored as PostgreSQL `TIMESTAMPTZ`.

## Migration runner

Implement a simple versioned SQL runner under `app_v2/db/`.

Requirements:

- discovers ordered `NNNN_*.sql` files;
- creates/uses `schema_migrations`;
- stores migration version, filename, SHA-256 checksum and applied timestamp;
- skips a migration already applied with the same checksum;
- raises a clear error if an already-applied migration file checksum changes;
- rolls back a failed migration;
- repeated runner invocation is safe;
- accepts an explicit database URL and does not depend on v1 config/schema.

## PostgreSQL adapter

`app_v2/adapters/postgres.py` should provide the minimal v2 connection boundary. Do not add repositories or business logic yet.

## Integration tests

Tests should cover:

- migration discovery / ordering;
- missing database URL produces a clear error;
- on a real PostgreSQL URL (when `NENOY_V2_TEST_DATABASE_URL` is supplied):
  - clean database migrates from zero;
  - second migration run is safe;
  - expected tables exist;
  - required unique constraints reject duplicates;
  - required queue/scope indexes exist.

A live database test may skip when the dedicated test URL is absent, but the schema must still be validated against a disposable PostgreSQL database before merge.

## Acceptance criteria

- [ ] versioned SQL migration exists;
- [ ] all MVP tables exist after migration;
- [ ] required constraints/indexes are present;
- [ ] migration runner is repeat-safe;
- [ ] checksum drift is detected;
- [ ] no ORM/Alembic added;
- [ ] no v1 DB/schema imports;
- [ ] v2 test suite remains green;
- [ ] schema was exercised against disposable PostgreSQL before merge.

## Required report

1. What changed
2. Exact checks/tests actually run
3. Manual checks remaining
4. Anything not completed
5. Errors/risks
6. Changes outside scope
7. Exactly one next recommended step (`TASK 04 — Telegram Update Normalizer + Webhook` if successful)
