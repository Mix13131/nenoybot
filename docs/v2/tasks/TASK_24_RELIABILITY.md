# TASK 24 — Reliability / Failure Scenarios

## Goal
Verify and harden safe degradation, retry, restart, and idempotency behavior before deployment.

## Required scenarios
- classifier/OpenAI unavailable;
- malformed classifier output;
- memory retrieval unavailable;
- Telegram 5xx/timeout;
- event worker restart with stale processing lease;
- event retry;
- outbox retry;
- duplicate webhook;
- duplicate scheduler tick;
- repeated processing does not create duplicate user-visible messages.

## Expected behavior
- uncertain Group unsolicited behavior becomes quieter/silent;
- explicit Group mention/reply still uses deterministic routing;
- Personal can continue without LONG-memory claims when memory retrieval is unavailable;
- Group callback is never fabricated when memory is unavailable;
- generation failure does not enqueue technical garbage or duplicate outbound;
- retries and restarts preserve deterministic dedupe.

## Acceptance
Full `tests_v2` green with dedicated reliability scenario coverage.
