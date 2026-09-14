# TASK 26 — Personal Smoke Test

## Goal
Formally verify the first live Personal НеНой 2.0 path in the isolated Railway v2 runtime.

## Evidence captured on 2026-09-14
- Telegram delivered a real private update to `/webhooks/telegram` and Railway returned HTTP 200.
- `nenoy-v2-worker` consumed the persisted event.
- Scene analysis returned successfully from OpenAI.
- Response generation returned successfully from OpenAI.
- Outbox sender delivered the generated reply through Telegram and Telegram returned HTTP 200.
- The user confirmed receipt of the first real НеНой 2.0 response.

## Findings from first live pass
1. Transport INFO logging exposed credential-bearing Telegram Bot API URLs in private Railway logs. Fixed separately in PR #73 by forcing HTTP transport loggers to WARNING+.
2. One Memory Mapper Structured Outputs request returned HTTP 400 while the main reply path remained successful. Root cause: an unconstrained object node in the strict JSON schema. Fixed in follow-up `fix/v2-memory-mapper-schema`.
3. Telegram bot token rotation is still required because the old token appeared in private runtime logs before the logging fix.

## Acceptance
- [x] real Telegram private update reaches v2 webhook
- [x] webhook persists and queues event
- [x] worker processes event
- [x] generator produces reply
- [x] outbox sends exactly one Telegram response
- [x] user confirms response receipt
- [x] production failures found during smoke test are converted into tracked fixes
- [ ] bot token rotated after private-log exposure

## Result
Functional Personal E2E smoke test: **PASS**.
Security cleanup: **pending token rotation by owner**.

Do not treat this as the full Friends Test. Group rollout remains gated by TASK 27 preflight.
