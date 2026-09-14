# TASK 06 — Outbox + Telegram Sender

## Status

**IN PROGRESS**

## Goal

Create reliable outbound delivery so future generators/services enqueue `OutboundMessage` records and never call Telegram directly.

## Branch

`task/v2-06-outbox-sender`

## Scope

- `app_v2/domain/**` only if a non-breaking addition is required
- `app_v2/repositories/outbox_repo.py`
- `app_v2/adapters/telegram_sender.py`
- `app_v2/workers/outbox_worker.py`
- `tests_v2/**`
- this task file

## Requirements

- enqueue is idempotent by unique `dedupe_key`;
- claim uses PostgreSQL `FOR UPDATE SKIP LOCKED`;
- claimed record has a processing lease via `available_at`;
- Telegram sender supports text and optional `reply_to_message_id`;
- Telegram HTTP/API errors are raised with useful context and persisted by retry path;
- retry/backoff and terminal failure are supported;
- successful send marks outbox row `sent` and sets `sent_at`;
- no generator/service sends Telegram directly.

## Configuration

Sender accepts an explicit bot token for tests and otherwise reads `NENOY_V2_TELEGRAM_BOT_TOKEN`. Missing token must raise a clear configuration error. No real token is committed.

## Acceptance

- [ ] duplicate enqueue does not create a second row;
- [ ] mocked successful Telegram send marks record sent;
- [ ] mocked Telegram failure goes to retry/failed path;
- [ ] reply target is passed to Telegram request;
- [ ] backoff is capped;
- [ ] no direct Telegram send added elsewhere;
- [ ] Gate A infrastructure can be composed without LLM.

## Next step

On success: `TASK 07 — Deterministic Dispatcher Policy`.
