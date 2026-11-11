# TASK 37 — Telegram lab launch

## Scope

The user has created a new Telegram bot, a closed test group, and added the new bot token and model API key to the isolated Railway Group Lab service. Deliver a usable Telegram control plane for the already reviewed historical case pack. Do not modify the current production groups or deploy the legacy entry point.

## Runtime

- Entry point: `python -m app_v2.labs.telegram_lab`.
- Dedicated container/config: `app_v2/labs/Dockerfile`, `app_v2/labs/railway.toml`.
- One process and persistent volume `/data`; atomic JSON state, exclusive process lock.
- Telegram long polling only. An existing webhook causes startup to stop; it is never deleted or replaced automatically.
- No production runtime/repositories/DB/outbox/reminder services are constructed.
- Model generation reuses the existing `GroupBehaviorReplayRunner` and `education_community_v1`.

## Secrets and admission

Environment variables, never git:

- `NENOY_V2_TELEGRAM_BOT_TOKEN` (new lab bot only)
- `NENOY_V2_OPENAI_API_KEY`
- `NENOY_LAB_PAIRING_CODE` (at least 16 characters)
- `NENOY_LAB_APPROVED_PACK_SHA256` (digest of operator-reviewed file)
- optional `NENOY_LAB_DATA_DIR`, default `/data`
- existing `NENOY_V2_MODEL_*` overrides and model timeout are supported

A group administrator sends `/lab_bind <code>` once. Only that chat and that operator can upload or run test packs. Pairing is durable; another chat cannot take it over. Unknown chats receive no content.

File admission uses an exact approved SHA-256, a 2 MiB size cap and strict bounded case validation. Full raw exports, ZIPs and edited/unreviewed packs are rejected before model calls. The model sees only current message plus reviewed past-only context; evaluation rubric and curation notes are removed. Private archives, case content and secrets must never be committed to this repository.

## Operator workflow

1. Bind the closed test group using the one-time pairing code supplied out of band.
2. Attach the reviewed `review_pack_checked.json`.
3. Press `Следующий кейс` or `Прогнать выборку`.
4. Read the HTML report returned to the same group. `/lab_status` and `/lab_report` are available.

Uploading is not generation. Full replay is explicitly initiated by the operator. A completed case is not rerun on repeated commands or file uploads. In-flight work after a restart is recorded as interrupted rather than silently repeated. A model API failure is never counted as a valid decision to stay silent; the batch stops after the first failed case.

## Honest limits

This launch is reviewed historical replay only. It is not automatic raw-history ingestion, incremental archive sync, a fully stateful community assistant, or general live group chat. LONG memory, callback retrieval, dynamic cooldown/feedback, reminders/actions remain absent exactly as documented by the existing replay runner. Synthetic API startup probe verifies connectivity but is not historical acceptance. Real case answers only exist after the reviewed pack is uploaded and run.

## Verification before deployment

Run full `pytest tests_v2 -q` on the PR tree. Added synthetic tests cover approved-file admission, omission of rubric, future-context rejection, pairing and cross-chat isolation, duplicate upload preservation, error versus silence, HTML escaping, interrupted runs, and injected current-Brain replay. No real dataset in CI.

Deploy this feature branch to the isolated lab only after green CI. Keep v2/main and the live Railway project unchanged during the first lab launch.
