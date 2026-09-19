# TASK 30 — Calendar reminders with timezone-safe persisted schedules

Issue: #94
Base: `v2`
Starting base SHA after TASK 29 completion: `aa43b5dd4872decbf749875f504145bf58a3f3c8`

## Goal

Implement the next product step after TASK 29: natural calendar reminder requests must produce a real persisted schedule, an honest reminder receipt, an actual due/outbox fire, and a timezone-aware next occurrence.

Primary acceptance flow:

`каждый день в 9 утра присылай короткую бодрую фразу`
→ timezone missing → clarification
→ `по московскому времени`
→ exactly one persisted recurring schedule using IANA `Europe/Moscow`
→ response may confirm only after persistence and must carry actual reminder action state / next occurrence
→ worker fires at local 09:00
→ next run is computed as the next local calendar occurrence, not blind `+86400`.

## Required MVP formats

- `каждый день в 9:00`
- `каждое утро в 9`
- `по будням в 08:30`
- `каждую пятницу в 18:00`
- calendar one-shot: `сегодня в 18:00`, `завтра в 09:00`
- preserve existing interval formats: `через N минут/часов`, `каждые N минут/часов`

Prefer deterministic parsing and code validation. Do not build an uncontrolled LLM-only date parser. Ambiguous time/timezone must fail closed to clarification.

## Timezone rules

- Persist IANA timezone names, not only UTC offsets.
- Explicit timezone in the current request/clarification wins over any stored default.
- If timezone is absent or ambiguous, do not create a reminder.
- Calendar recurrence must remain tied to local wall-clock time across DST.
- Do not use Moscow as a global default.
- Add deterministic handling/tests for skipped/duplicated local wall times.

## Persistence / compatibility

Use the smallest safe extension of the existing reminder model.

- Existing interval reminders must remain fully compatible.
- Calendar recurrence must have an unambiguous validated representation distinct from `interval:<seconds>`.
- Persist enough information to prove scope, timezone, recurrence/calendar spec, UTC due_at, user-facing local schedule, source provenance, payload text/context, status/attempt lifecycle.
- A migration is allowed only if required; document backward compatibility.
- Do not introduce new infrastructure.

## Honest receipts / clarification

Use the TASK 29 foundation now present in `v2`.

- No `scheduled/succeeded` claim without persisted reminder ID.
- `needs_clarification` must reach the generator as a real reminder action state.
- DB failure => failed, never “буду напоминать”.
- Dedupe retry/no-op must not claim a newly created schedule.
- After successful persistence, action state must include actual reminder ID and next occurrence even if user-facing text does not expose the ID.

## Dedupe / follow-up clarification

- Retry of one Telegram event/update creates one schedule chain.
- A timezone clarification reply must complete the pending intent, not create an independent duplicate schedule.
- Do not replay historical chat commands.
- Preserve current cancellation semantics from D-046/D-047/D-048; no Stop button.
- If an unambiguous reschedule path already exists and can be extended safely, support `теперь в 10 утра`; otherwise fail closed rather than mutating an arbitrary reminder. Do not expand scope into a full task manager.

## Worker / recurrence

- Worker fires from persisted state, not from previous model prose.
- After successful recurring fire, compute the next calendar occurrence timezone-aware.
- Preserve existing retry lifecycle on failed send; do not fork a second chain.
- Existing Group cancellation controls must cancel calendar reminders too.
- Scope isolation is mandatory.

## Required tests

Synthetic data only.

At minimum:
- daily 09:00 Europe/Moscow;
- every morning 09:00 equivalent;
- weekdays 08:30;
- Friday 18:00;
- timezone clarification flow;
- explicit timezone override;
- DST transition regression;
- one-shot today/tomorrow;
- retry/dedupe;
- persistence failure => no success claim;
- existing interval reminders unchanged;
- cancellation of calendar chain;
- worker computes next occurrence correctly after fire;
- failed send preserves retry lifecycle;
- scope isolation;
- persisted reminder receipt is passed to generator;
- PostgreSQL integration for persistence/dedupe/worker paths.

## Hard boundaries

Do NOT implement:
- full task manager;
- history analysis / auto-adaptation;
- arbitrary background automations;
- replay of historical commands;
- new Stop-button UI;
- deploy, Railway changes, production DB changes, or live Telegram tests.

Do not modify legacy `app/` or `tests/`.

## Required validation

Run:
- `bash scripts/codex_preflight.sh`
- `python -c "import app_v2"`
- full `pytest tests_v2 -q` with isolated `NENOY_V2_TEST_DATABASE_URL`
- `git diff --check`
- `git diff --name-only v2...HEAD`

For persistence/dedupe/worker behavior, PostgreSQL tests must actually execute; skips are not acceptance.

Finish with exactly the seven AGENTS.md report sections. Leave the PR Draft/open. No merge or deploy.


## Calendar recurrence policy

- Calendar recurrence from phrases such as `каждый день`, `по будням` and `каждую пятницу` continues until an explicit existing cancellation control stops the chain.
- The legacy safety cap of 4 occurrences remains only for frequent interval reminders such as `каждые 30 минут` / `каждый час`.
- Calendar recurrence must not silently stop after four successful fires.


## TASK 29 completion gate

TASK 29 / issue #92 is completed in `v2` at `aa43b5dd4872decbf749875f504145bf58a3f3c8`. Final TASK 30 acceptance CI must run against this updated base before merge.
