# TASK 25 — Railway v2 Deployment

## Goal
Deploy a fully isolated НеНой 2.0 runtime without modifying v1/staging.

## Runtime topology
- `nenoy-v2-web` — FastAPI webhook + health/readiness
- `nenoy-v2-worker` — event queue + reminders + outbox + maintenance
- separate PostgreSQL — never reuse v1 DB
- separate Telegram v2 bot token/webhook

## Deployment commands
Web:
`uvicorn app_v2.main:app --host 0.0.0.0 --port $PORT`

Worker:
`python -m app_v2.workers.main`

Pre-deploy migration:
`python -m app_v2.db.migrations`

## Production requirements
- `NENOY_V2_ENV=production`
- `NENOY_V2_DATABASE_URL`
- `NENOY_V2_WEBHOOK_SECRET`
- `NENOY_V2_TELEGRAM_BOT_TOKEN`
- `NENOY_V2_TELEGRAM_BOT_USERNAME` or `NENOY_V2_TELEGRAM_BOT_USER_ID`
- `NENOY_V2_OPENAI_API_KEY`
- model role variables as needed

## Safety
Existing Railway project `nenoybot-staging` / service `worker-staging` is v1/staging and must not be edited by this task.

## Acceptance
- production config rejects incomplete credentials;
- `/ready` actually probes PostgreSQL;
- event worker uses the real Personal/Group handler;
- reminders are fired into the normal event queue;
- outbox is continuously delivered by Telegram sender;
- meaningful approved Group messages can build Group memory while remaining silent;
- full `tests_v2` green;
- isolated Railway resources are deployed before Gate D is closed.
