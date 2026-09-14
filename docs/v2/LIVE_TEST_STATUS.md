# НеНой 2.0 — Live Test Status

Updated: 2026-09-14

## Runtime
- Railway project: `nenoy-v2`
- `nenoy-v2-web`: live
- `nenoy-v2-worker`: live
- PostgreSQL: live
- Telegram webhook: live

## Personal live smoke
Status: **functional PASS**.

Observed real path:
`Telegram -> webhook 200 -> PostgreSQL event -> worker -> OpenAI -> outbox -> Telegram sendMessage 200 -> user received reply`.

## Known follow-ups
- Telegram bot token rotation is required because the first live worker INFO logs contained the credential-bearing Bot API URL before transport logging was hardened.
- Memory Mapper strict JSON schema produced one HTTP 400 on the first live message; a schema hardening fix is in progress.

## Next rollout gate
TASK 27 Friends Test Preflight. No broad Group rollout before explicit group selection + whitelist.
