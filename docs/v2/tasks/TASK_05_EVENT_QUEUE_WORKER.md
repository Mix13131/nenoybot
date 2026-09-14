# TASK 05 — PostgreSQL Event Queue + Worker Claim

## Status

**IN PROGRESS**

## Goal

Implement durable v2 event processing on PostgreSQL only: atomic claim with `FOR UPDATE SKIP LOCKED`, processing lease, completion, retry/backoff, terminal failure and stale-processing recovery.

## Branch

`task/v2-05-event-queue-worker`

## Scope

- `app_v2/repositories/event_repo.py`
- `app_v2/workers/event_worker.py`
- `app_v2/workers/main.py`
- `tests_v2/integration/**`
- this task file

No Redis/Celery, LLM, Dispatcher, Memory or Telegram outbound.

## Queue lifecycle

`pending -> processing -> completed`

Failures: `processing -> retry -> processing`, terminally `-> failed`.

`events.available_at` doubles as the next-attempt time and, while processing, the processing-lease deadline. This avoids adding schema columns before real evidence requires them.

## Requirements

- atomic single-row claim ordered by creation time;
- claim uses `FOR UPDATE SKIP LOCKED`;
- claim increments `attempt_count`;
- processing lease has configurable timeout;
- retry uses exponential backoff capped by configurable upper bound;
- maximum attempts move event to `failed`;
- stale processing rows whose lease expired are recovered to retry/failed;
- completion is idempotent;
- handler may be no-op/stub for TASK 05;
- worker loop must remain independent from web process.

## Acceptance

- [ ] claim SQL is atomic and uses `SKIP LOCKED`;
- [ ] worker completion path tested;
- [ ] worker retry path tested;
- [ ] retry delay is capped;
- [ ] stale recovery tested;
- [ ] optional live Postgres concurrency test proves one event is not claimed twice;
- [ ] no Redis/Celery introduced;
- [ ] v1 untouched.

## Next step

On success: `TASK 06 — Outbox + Telegram Sender`.
