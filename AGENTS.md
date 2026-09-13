# AGENTS.md — НеНой 2.0

This repository contains two product lines. Treat their separation as a hard rule.

## Product lines

- `main` / legacy `app/` and `tests/`: НеНой v1. Do not modify unless a task explicitly permits it.
- `v2` / new `app_v2/` and `tests_v2/`: НеНой 2.0 active development.

The v2 source of truth lives in `docs/v2/`.

## Before editing anything

1. Run `bash scripts/codex_preflight.sh` if the script exists.
2. Read the task file named in the user prompt.
3. Read only the v2 specs required by that task; if the task lists required docs, read all of them.
4. Confirm the current branch. Task branches should derive from `v2`.
5. Inspect `git status --short` and preserve unrelated user changes.
6. If repository state or environment contradicts the task, stop and report the mismatch before editing.

## Scope discipline

- One Codex task = one bounded change.
- Do not start the next task automatically.
- Do not perform opportunistic refactors outside scope.
- Do not rename public contracts or change product policy unless the current task explicitly requires it.
- Do not add Redis, Celery, Kafka, RabbitMQ, a graph DB, a dedicated vector DB, Kubernetes, frontend/admin UI, or other infrastructure unless an approved task explicitly requires it.
- Never put real secrets, tokens, chat IDs, API keys, credentials, or production data in the repository.

## v2 architecture invariants

- New runtime code goes under `app_v2/`.
- New tests go under `tests_v2/`.
- `app/` is legacy/reference code, not a base class for v2.
- Personal and Group memory must stay isolated by scope.
- Group retrieval must never fall back to Personal memory.
- Telegram webhook paths must remain fast and must not wait for LLM work once the async pipeline is implemented.
- PostgreSQL is the MVP database and durable queue; do not introduce extra queue infrastructure without evidence.
- Silence is a valid Group outcome. Do not turn every event into a response.
- Strong/expensive models are used only after policy decides that generation is needed.

## Coding style

- Prefer small typed Python modules with explicit dependencies.
- Domain code must not depend on Telegram, Railway, or a specific LLM vendor.
- Favor standard library and existing dependencies before adding packages.
- Keep configuration explicit and environment-driven.
- Fail with clear configuration errors rather than opaque stack traces.
- Build deterministic logic as deterministic code; use LLM calls only where semantic judgment is needed.

## Validation

Run the checks required by the current task. Never claim a check passed unless you actually ran it.

For v2 tasks, use these when applicable:

```bash
python -c "import app_v2"
pytest tests_v2 -q
git diff --name-only v2...HEAD
```

The last command is a scope check: legacy `app/` and `tests/` must remain untouched unless explicitly allowed.

If a required dependency or sandbox limitation prevents a check, report the exact command and exact failure. Do not replace execution evidence with an assumption.

## Mandatory completion report

End every implementation task with exactly these sections:

1. `## 1. Что сделано`
2. `## 2. Как проверено` — include every command actually run and its result
3. `## 3. Что проверить вручную`
4. `## 4. Что не сделано`
5. `## 5. Ошибки и риски`
6. `## 6. Изменения вне задачи`
7. `## 7. Следующий рекомендуемый шаг` — exactly one next step

Do not hide warnings or failed checks. Do not say tests passed if they were not run.

## Current build process

Follow `docs/v2/MVP_BUILD_PLAN.md`. Each task has its own acceptance criteria and is reviewed before the next one starts.
