# TASK 43 — Zero-config group onboarding: final transition contract

Approved 2026-09-28; bounded completion of PR #155 from `v2`.

## Current authoritative policy (D-055 clarification)

This final transition contract supersedes the initial PR #155 text and the older D-055/Architecture wording that a new boolean can recover historical operator intent. It cannot.

1. A newly inserted Telegram group/supergroup is automatically active and whitelisted. It receives a fresh copy of the already accepted Friends Day-1 profile (low initiative, existing safety and daily limits). Private chats and channels are not auto-approved.
2. Ordinary ingest never overwrites access flags, populated group profiles, silence, membership profiles or stored memory on conflict. Thus ALL existing denials survive traffic, including older administrative paths that only write `is_whitelisted=false`.
3. Migration 0007 is additive and preserves every existing denied group. It records `group_access_blocked=true` and a separate `group_legacy_reconcile_pending=true` marker. Existing allowed/inactive/profile/silence states otherwise remain unchanged.
4. There is no durable historical denial provenance. Do not infer it from timestamps, empty profiles or missing messages. The transition is deliberately conservative, not an assertion that all old denied groups were deliberately blocked.
5. One operator-authorized legacy group can be reconciled by exact UNIQUE title and, optionally, its matching ID. The lookup and update are atomic, have no list limit, and reject zero/multiple matches or inactive groups. Only pending legacy denials qualify. The pending marker is consumed; any later administrative change also clears it. Retained deployment settings cannot resurrect a subsequently blocked group.
6. Reconciliation preserves nonempty group profiles, member data, memory, silence and unrelated rows. An explicit operator option may initialize ONLY an empty profile with the same Friends defaults; it does not reset an existing profile.
7. New groups after rollout need no per-group ID, env variable, database write by an operator, or bot admin role. One-time reconciliation of a group first seen before rollout is migration work performed by the operator, not the normal user onboarding flow.

## Runtime changes

- `app_v2/db/migrations/0007_zero_config_group_onboarding.sql`
- `app_v2/repositories/ingest_repo.py`: insert-only approval and default profile
- `app_v2/repositories/group_context_repo.py`: explicit administrative block and marker semantics
- `app_v2/domain/group_defaults.py`: fresh accepted Friends defaults, no behavior-engine rewrite
- `app_v2/deploy_prepare.py`: migrations, schema verification, optional authorized reconciliation; sanitized receipts only
- `app_v2/db/migrations.py`: serialize simultaneous web/worker migration runs with a connection-scoped advisory lock (60-second lock timeout)

## Deployment

Both services must run `python -m app_v2.deploy_prepare` before the new revision can serve/process traffic. Web then runs `python -m app_v2.telegram_webhook_setup`. Do not assume the worker starts before web. A nonzero pre-deploy exit must stop the deployment.

Optional TEMPORARY worker-only variables for one approved pre-rollout group:
- `NENOY_V2_RECONCILE_LEGACY_GROUP_TITLE`: exact unique title, configured only by the operator, never read from Telegram message text;
- `NENOY_V2_RECONCILE_LEGACY_GROUP_ID`: optional consistency check when known;
- `NENOY_V2_RECONCILE_INITIALIZE_EMPTY_PROFILE=true`: explicitly authorize initialization of an empty profile.

Clear temporary variables after the sanitized `reconciled` receipt. Never publish their real values. Leave all other services, secrets, source branches and pending environment changes untouched.

Rollback: the previous application revision can run with the additive columns present. Do not delete rows or edit applied migration checksums. Reverting the application does not undo a specifically authorized group activation.

## Acceptance and evidence

Run `bash scripts/codex_preflight.sh`, `python -c "import app_v2"`, `pytest tests_v2 -q` with isolated PostgreSQL 16, and `git diff --check` / scope check. New PostgreSQL tests must execute rather than skip: old denied/allowed/inactive rows, exact/ambiguous/mismatched reconciliation, profile/member/silence preservation, fresh group/supergroup, private/channel, all disable paths, repeated updates and simultaneous migrations.

Code/CI/deployment/live are separate statuses. No `LIVE PASS` until an actual post-deploy Telegram message gets an answer in the user's existing group. No real group titles, IDs, messages or secrets in fixtures/reports.
