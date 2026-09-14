# TASK 04 — Telegram Update Normalizer + Webhook

## Status

**IN PROGRESS**

## Goal

Accept Telegram webhook updates, normalize supported update shapes into the v2 `EventEnvelope`, persist identities/raw messages/events in the isolated v2 PostgreSQL schema, and return quickly without any LLM call.

## Base / branch

- Base: `v2`
- Work branch: `task/v2-04-telegram-webhook`

## Allowed scope

- `app_v2/main.py`
- `app_v2/adapters/telegram_webhook.py`
- `app_v2/services/event_ingestor.py`
- required `app_v2/repositories/**`
- `tests_v2/integration/**`
- this task file

No OpenAI/model calls. No Dispatcher, Memory Mapper, Response Generator, worker loop, outbox delivery or v1 changes.

## Supported updates

At minimum:

- private text message;
- group/supergroup text message;
- command text;
- reply-to-bot message;
- mention metadata from message entities;
- edited message;
- message reaction add/remove when present;
- unknown update: safe `ignored` result, HTTP 200.

## Webhook

Endpoint: `POST /webhooks/telegram`.

- validate `X-Telegram-Bot-Api-Secret-Token` when `NENOY_V2_WEBHOOK_SECRET` is configured;
- invalid/missing configured secret -> HTTP 401;
- no LLM/network calls;
- successful/duplicate/ignored update -> HTTP 200;
- DB failure must not be silently reported as success.

## Persistence

For supported message events:

1. upsert Telegram user;
2. upsert chat;
3. upsert membership for group chats;
4. store raw message with unique `(chat_id, telegram_message_id)`;
5. insert normalized pending event with unique `telegram_update_id` and stable `event_id`.

Duplicates must not create a second event or second raw message.

`EventEnvelope` stays Telegram-agnostic: Telegram-specific detail belongs in `metadata`/persistence adapter, not new Telegram domain types.

## Acceptance

- [ ] pure normalizer tests cover private/group/reply/edited/reaction/unknown;
- [ ] webhook secret tests cover accepted/rejected requests;
- [ ] duplicate update is idempotent;
- [ ] private/group fixtures map to correct scope/event types;
- [ ] webhook never calls OpenAI;
- [ ] unknown update returns 200/ignored;
- [ ] legacy `app/` and `tests/` unchanged;
- [ ] v2 tests remain green.

## Required report

1. What changed
2. Exact checks/tests actually run
3. Manual checks remaining
4. Anything not completed
5. Errors/risks
6. Changes outside scope
7. Exactly one next recommended step (`TASK 05 — PostgreSQL Event Queue + Worker Claim` if successful)
