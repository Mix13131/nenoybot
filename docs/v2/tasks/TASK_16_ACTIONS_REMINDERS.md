# TASK 16 — Action Engine + Reminders

## Goal
Add real Personal actions required for the MVP: tasks and reliable one-time reminders.

## MVP actions
- create/update/complete task;
- create/cancel/reschedule reminder;
- due reminder becomes a normal `reminder_due` event and re-enters EventWorker/Dispatcher;
- recurring reminders are explicitly unsupported until a later task.

## Reliability rules
- `due_at` must be timezone-aware;
- state is PostgreSQL-backed and restart-safe;
- scheduler uses DB due rows, not in-memory timers;
- event id is deterministic: `reminder:<id>:<due_at-iso>`;
- `events.event_id UNIQUE` makes duplicate scheduler ticks idempotent;
- scheduler does not send Telegram directly.

## Acceptance
- task lifecycle;
- one-time reminder lifecycle;
- cancel/reschedule;
- naive datetime rejected;
- recurrence rejected explicitly;
- due event persistence can survive process restart;
- duplicate scheduler tick cannot duplicate `reminder_due` event.

Do not start TASK 17 before review.