# TASK 44 — Personal tasks/reminders: conversation to durable action to delivery

Status: APPROVED IMPLEMENTATION TASK; implementation/CI/deploy/live acceptance NOT YET DONE.
Date: 2026-09-29. Base inspected: v2@d85cc877a5a845a1899a459ac4b7b9fa047600e1.
Branch: fix/v2-personal-actions-e2e. Target: v2. This file is a task, not an implemented feature.

## Goal and observed defect

Fix the existing Personal v2 conversation path so an explicit private request actually creates a task or reminder and a due reminder is delivered to that same private chat. Do not repair this by changing Telegram permissions or requiring per-user environment configuration.

At the inspected revision:
- app_v2/runtime.py creates ActionEngine but does not inject an action service into PersonalPipeline.
- app_v2/services/personal_pipeline.py calls personal_operation_receipts(mapper_result) without executing task/reminder operations.
- app_v2/services/operation_receipts.py unconditionally supplies task/reminder not-wired receipts for Personal.
- Group has separate working scheduling integration. Personal conversation being LIVE does not prove Personal actions are wired.

Synthetic reproduction (not private user content): a person supplies a supplier-document check with a deadline; the bot repeatedly requests date confirmation, then admits it cannot create anything. A later 'создай задачу теперь' still does not create a task. Do not publish real screenshots, names, chat IDs, suppliers, invoices, credentials or private messages.

## Before coding

Run scripts/codex_preflight.sh; inspect branch and working tree; read AGENTS.md. Read required current docs: this task, ARCHITECTURE.md (scope/actions/queue), MVP_BUILD_PLAN.md TASK 16, DECISIONS.md (truthful receipts/calendar/scheduled actions), LIVE_TEST_STATUS.md, TASK_25_RAILWAY_DEPLOYMENT.md. Read existing runtime, PersonalPipeline, ActionEngine, task/reminder repositories, calendar parser/pending-intent implementation, Group scheduling adapter, scheduler, event/outbox workers and applicable tests. Verify actual contracts rather than assuming feature parity from older PASS labels.

## Bounded implementation

1. Wire a Personal action orchestrator into the actual production runtime. Supported minimum: create_task; create one-shot reminder; cancel and reschedule an owned reminder. Creating a task alone must not be claimed to schedule a notification. A request for a reminder must not silently become only a memory card or task. Support an explicit request for both with truthful per-operation receipts.
2. Reuse existing deterministic time parsing, durable reminders, scheduler and outbox where safe. Do NOT impersonate a Group event to reuse GroupReminderService, copy group identity into Personal, or loosen Group security. Small shared extraction is allowed only with Group regression coverage.
3. Retain a bounded, durable, Personal-scoped pending action for necessary clarifications and follow-ups such as 'да, верно', 'создай теперь', 'в 15:00', 'по Москве'. Bind it to the original request and user; TTL/consumption/restart must be handled. Do not execute quoted, ambient, third-party or retrieved instructions. An ambiguous 'да' without one clear pending action must not mutate data. Explicitly requested simple actions do not need another confirmation when all inputs are known.
4. 'Завтра' is resolved from the original Telegram event timestamp in the known user's timezone, not from server date or model guesses. A timezone comes from verified Personal state, an explicit utterance or an already documented default (shown in the receipt); do not infer it from device/VPN, language or group membership. If unknown, ask once for timezone and resume the same action. Do not ask to confirm the calendar date when relative date and timezone already determine it. A late clarification must not move an already anchored date to another day.
5. Preserve deadline versus notification semantics: 'создай задачу ... срок завтра до 15:00' creates a task with deadline; 'напомни завтра в 15:00 ...' creates a reminder at that time. For genuinely ambiguous notification timing, ask one focused question rather than guessing or withholding a separately valid requested task. Explicit past/ambiguous times fail clearly; do not silently roll into another day/year.
6. Persist changes before confirming success. Replace Personal not-wired receipts only for operations actually executed. Include durable ID, operation, outcome, due time/timezone and changed/already-existing semantics as appropriate. A generator failure must not roll a successful action into an untruthful 'nothing happened' claim or create duplicates on retry. A memory write is NOT a task/reminder receipt. Check capability before collecting parameters; report unavailable/failed promptly without asking for irrelevant confirmations or blaming the user.
7. Idempotency must cover duplicate Telegram updates, event retries and retry after a DB commit/outbox failure. Reuse a stable originating action key and actual durable constraints/transactions; a text-only dedupe must not suppress a legitimate later separate request. Check ActionEngine/TaskRepository write safety before exposing existing methods. Cancellation/rescheduling must verify personal scope and owner in the database operation, not trust a model-provided ID. Do not broaden task update/complete features in this patch.
8. Verify the WHOLE due path: durable reminder -> ReminderScheduler -> reminder_due event -> RuntimeEventHandler/PersonalPipeline or safe dedicated due handler -> outbox -> TelegramSender to the same private chat. The due event must deliver the saved reminder content, not interpret it as a new user request, re-create it, demand more parameters or invent an external check. Cancelled reminders suppress pending sends under existing cancellation/retry guarantees; document any unavoidable already-in-flight boundary honestly.
9. Personal preferences, pending actions, receipts, tasks, reminders and retrieval must remain isolated per user and from every Group. Do not use unscoped cancel/update helpers on model-provided IDs. Group scheduling, memory privacy and zero-config onboarding must retain existing behavior.

## Allowed files and exclusions

Allowed: necessary app_v2 runtime/services/domain/repository/worker wiring, matching tests_v2 files, a NEW additive migration only if required for durable pending actions/idempotency (do not edit deployed 0001-0007), relevant v2 docs and release notes. Explain each changed file and any shared extraction.

Forbidden: main, app/**, tests/**, production database/configuration, live Telegram sends from the coding task, new infrastructures/services, external calendar integrations, unrelated UI/roast/initiative changes. Do not add the rejected inline Stop button. Recurrent schedules, autonomous supplier/email checks and full task-manager UX are not part of this repair; unsupported requests must be explicit rather than producing false promises.

## Acceptance and tests

Use synthetic identities and frozen clocks. Test the public/production-wired path, not just a directly constructed helper.

- Known timezone Europe/Moscow, original event 2026-09-29 18:00 local: 'Напомни завтра в 15:00 проверить документ от поставщика' persists exactly one reminder due 2026-09-30 15:00 local and confirms the same date/time/timezone without redundant date confirmation.
- 'Создай задачу: проверить документ от поставщика, срок завтра до 15:00' persists a task with its deadline. It is not falsely described as a scheduled notification. Explicit reminder request, including a follow-up, creates a real reminder as well.
- A previously discussed, uniquely identifiable draft + 'создай задачу теперь' uses that draft; repeated confirmation/retry does not duplicate it. 'Да' with no valid/expired/ambiguous pending action does not create anything.
- Unknown timezone -> exactly one necessary question -> timezone reply resumes the original action. Cover reply after midnight and worker restart without changing the anchored intended date.
- Read rows from PostgreSQL for the requesting user. Advance test clock, run scheduler/event/outbox workers and record the sender call: correct chat, original reminder content, one due delivery for duplicate ticks/retries. Mock external OpenAI/Telegram in automated tests only; no real external writes.
- Cancel/reschedule affects only the intended owned reminder; the old due time no longer fires. Wrong-user and Group IDs cannot change/read other users' records. At least two Personal users plus one Group are exercised.
- Duplicate input, retry after persistence, persistence failure and generator/outbox failure: truthful receipts, no duplicate task/reminder, pending intent remains consistent. For an ambiguous real Telegram transport timeout retain the existing honest delivery guarantee, not a new unsupported exactly-once claim.
- Direct due event is not reclassified as a creation request; original request is not written to long-term memory as a completed action.
- Existing Group tests, onboarding tests and ordinary Personal reply/memory tests still pass.

Required commands: python -c "import app_v2"; python -m compileall -q app_v2 tests_v2; targeted new tests; pytest tests_v2 -q with NENOY_V2_TEST_DATABASE_URL pointing to isolated PostgreSQL; git diff --check; git diff --name-only v2...HEAD. The new PostgreSQL behavioral tests must actually run, not skip. Report blocked dependency installation accurately; do not replace a real run with guessed PASS.

## Publication and release gates

Implement and PUSH to the existing PR branch. Return the FULL commit SHA visible in GitHub, changed files, actual commands and results. A local commit, draft PR proposal, prose report or passing CI on this initial docs-only commit is NOT evidence of a shipped repair. If push is unavailable, say so and return the actual patch/file contents through the authorized task output; do not pretend this branch contains unpublished work. Never place secrets or production data in that output.

Keep this PR draft until code and full PostgreSQL CI are present. Do not merge/deploy in the coding task. Review only the task delta and known acceptance blockers; no repeated full-system hardening loop. Once implemented and verified, release separately through v2, check actual deployed SHAs/schema/worker health, then perform a real user-initiated private 'напомни через 2 минуты: тест' smoke. LIVE PASS requires an actual received due message, not just 'создано'. Update LIVE_TEST_STATUS with separate conversation, Personal action, Group action, CI and live statuses.

## Mandatory completion report

1. Что сделано — exact implementation and files.
2. Как проверено — every command actually run, counts passed/failed/skipped and CI commit/run IDs.
3. Что проверить вручную — only unavailable automatic evidence, including actual private reminder delivery.
4. Что не сделано — explicit limitations and whether code is pushed/merged/deployed.
5. Ошибки и риски — no concealed failures, assumptions or unsupported guarantees.
6. Изменения вне задачи — list and justify; otherwise 'Нет'.
7. Следующий рекомендуемый шаг — exactly one next step.
