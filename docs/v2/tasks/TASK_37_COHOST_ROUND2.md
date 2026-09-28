# TASK 37 — isolated cohost revision + second round

## Approved scope

The user approved fixing the laboratory assistant role and testing the SAME reviewed case pack again, preserving the first results. Existing live groups and the shared education_community_v1 preset must not change.

## Implementation

Additive files under app_v2/labs only:

- cohost_replay.py: private education_cohost_lab_v2 revision; contextual structured planning (answer/clarify/route_admin/safety/silent), validated past source references, existing Event/Decision contracts, PersonalityEngine, ResponseGenerator, operation receipts and model adapter.
- community_cohost_v2.md: role-specific instruction, no mandatory banter, no authority to grant enrollment/access/payment decisions, no invented links or attachment content.
- telegram_lab_rounds.py: opt-in isolated entry point; inherits admitted upload, owner pairing and execution controls; adds second-round selection and reports.

No changes to app_v2/services/, app_v2/prompts/group_generator.md, connector registry, live GroupPipeline, v2/main refs, production DB/memory/reminders/outbox.

Important parity distinction: the shared model/adapter, generator and personality modules are reused. The lab's contextual planning and decision selection are a NEW opt-in behavioral policy, not unchanged production SceneAnalyzer/Dispatcher behavior. Do not describe this as changing only numeric personality knobs. This is not deployed into the live connector pipeline.

## Durable run storage

Existing /data/state.json, pack.json and results.json stay untouched. First startup publishes an atomic baseline snapshot under /data/runs/round01. The active round pointer lives in /data/lab_round_state.json. /lab_round2 creates /data/runs/round02 once, copying only binding/offset/pack and starting a distinct results file. Repeated selection never resets results or starts paid model calls.

Round manifests include revision, policy hash, model names, available deployment commit, approved upload digest and initial artifact hashes. A changed policy/model fingerprint refuses mixed-version continuation. The approved input file is unchanged. No private datasets, identifiers, tokens or outputs are committed.

## Operator workflow

Same bot and same bound group. No new upload, bot or pairing required.

1. /lab_round2 — select/create the second round (no model call).
2. Click 'Прогнать выборку' (or /lab_run) — explicitly start the new round.
3. Retrieve group_lab_round02.html. /lab_before retrieves the baseline.

The baseline is not rerun. Complete cases do not repeat. Interrupted/API-failed cases are not counted as valid silence. This bounded revision does not add arbitrary fresh-archive upload, live group conversation or automatic retries.

## Acceptance

- Context reaches the cohost planner before the reply/silence decision.
- Requests to admin do not become fake direct-to-bot flags.
- Unknown/future sources are rejected; member-only evidence cannot confirm admin facts.
- Enrollment/payment permissions stay with the organizer.
- Silent plans do not invoke the generator; admin announcements stay silent absent an explicit bot request.
- Rubrics/expected answers never enter model payloads.
- Existing preset/registry and core files remain unchanged.
- Baseline files remain byte-identical; round2 selection is idempotent, binding and results survive restart.
- Only the already paired owner/chat can operate the lab.
- Full PostgreSQL tests_v2 CI before deployment to the isolated Group Lab service.
- Real model quality is PENDING a separate second run on the same reviewed 28 cases.

## Validation environment

Repository code is read/written via the authenticated GitHub connector. Local git clone was attempted but failed with 'Could not resolve host: github.com'. Therefore do not claim a local checkout, codex_preflight or local full pytest execution. Full-suite execution evidence must come from GitHub Actions. No secrets are requested from the operator.

## Rollout

Only the separate Group Lab Railway project/service. Deploy branch feat/lab-cohost-round2 with start command python -m app_v2.labs.telegram_lab_rounds after green CI. Keep the existing persistent volume and all credentials. Never delete an existing webhook automatically. Do not merge this feature branch into v2 to obtain the lab deployment.
