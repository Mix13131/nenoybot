# TASK 37 — cohost usefulness correction / focused round 3

## Approved change — 2026-09-23

After reviewing the completed second historical run, the user approved one bounded laboratory correction:

- suppress empty public redirects when a person already asks the organizer and the assistant adds nothing;
- keep useful answers grounded in the organizer's existing messages, including questions addressed to admin;
- do not infer a one-off change merely because a recurring change was not confirmed;
- distinguish a peer suggestion from a permission request; do not invent group restrictions;
- avoid clarifying an already obvious event/topic when that clarification cannot supply the missing link.

Do not make the assistant globally quieter or change the model. Do not add live chat, long memory, archive synchronization, admin queues or real actions in this task.

## Isolation

Only additive lab/test/doc files relative to deployed a98a59b184016230f7d1c180fa61ffce5242fb39:

- `cohost_helpfulness.py`: explicitly separate `education_cohost_lab_v3` planner/runner using the same shared model adapter, domain contracts, generator and personality services;
- `community_cohost_v3.md`: role and source-scope instruction;
- `telegram_lab_focus.py`: owner-operated focused round 3 in the existing Telegram lab;
- synthetic tests and this runbook.

The prior `cohost_replay.py` (revision 2), old prompts, `education_community_v1` registry, production services and main/v2 refs must remain unchanged. The v3 lab runner deliberately reuses the v2 validation rules, including the current-reference bug fix. Do not describe these prompt changes as a mathematical guarantee of semantic classification or answer accuracy.

## Input and historical acceptance

Twelve cases are selected from the already reviewed 28-case pack. Their sanitized IDs are fixed in the evaluation harness only, never injected into model policy. Text/context of the cases remain unchanged; rubric and expected answers remain outside model inputs.

Targets: c001132 temporal scope, c006118 empty admin redirect, c003435 peer suggestion, c005544 unnecessary clarification.
Controls: c005312/c005343 confirmed schedule, c000168 available material, c000378 no invented admission permission, c000332 peer support, c000326 quoted teaching material, c000224 admin announcement/current-source regression, c000303 safety boundary.

This is a targeted 12-case regression check, NOT another full 28-case evaluation or an unbiased performance sample. Reports must say so. No private archive, case text, real outputs or credentials in git.

## Storage and restart behavior

On startup, prepare an atomic new `/data/runs/round03` from the COMPLETED second round. Verify its approved-upload lineage and source pack hash before selecting cases. Snapshot the current owner binding and polling offset. Record the selected input hash, parent manifest/results hashes, implementation fingerprint, model names and deployment commit.

Startup makes zero model calls. No raw upload or new pairing is accepted by this runner. `/data/runs/round01`, `/data/runs/round02`, their manifests/results/continuation records, and legacy `/data` artifacts are not modified.

Repeated commands do not rerun completed cases. `/lab_resume` explicitly retries failed/interrupted and pending cases only, snapshotting the failed attempt first. A changed policy/model/deployment cannot mix into this focused run, even via resume. API/schema failures are errors, not valid silence.

## Operator workflow

Same bot, same bound group, same admitted input. No new secrets, upload, pairing or BotFather changes.

- `/lab_check@BROConrol_bot` starts the 12 selected cases immediately.
- `/lab_report` retrieves `group_lab_round03.html`.
- `/lab_previous` retrieves preserved round 2; `/lab_before` retrieves round 1.
- `/lab_resume` explicitly retries unfinished work after a technical failure on the SAME implementation.

Ordinary group conversation is not enabled. No public group or admin invitation is part of this task.

## Deployment

Deploy only the existing isolated Railway `nenoy-group-lab` / `group-lab` service, preserving its `/data` volume, one replica, all credentials, Dockerfile and `/health` settings.

Source: `fix/lab-cohost-useful-replies`.
Start command: `python -m app_v2.labs.telegram_lab_focus`.

The lab Dockerfile and railway.toml retain defaults for the original runner; the above explicit Railway service override is REQUIRED for this opt-in revision. Do not merge to v2/main to deploy the lab. Do not take over or delete any webhook.

## Validation gate

Full PostgreSQL `pytest tests_v2 -q` must pass before deploy. Synthetic tests verify routing contracts, prompt wiring, source validation, unchanged v2 policy/registry, immutable old artifacts, fixed selection, owner-only single-command launch, no duplicate paid calls, version mismatch refusal, explicit error retry and report honesty/escaping.

These tests do not establish that the live model makes the desired semantic choices. Real acceptance remains pending the owner's focused run.

Local repository clone was attempted and failed with DNS `Could not resolve host: github.com`. No local full-suite/preflight result is claimed; authenticated GitHub reads/writes and Actions are the verification path.
