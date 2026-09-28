# TASK 43 — Final bounded rollout findings

Completes the two code findings from review of `966ede5`; no new subsystem audit.

## Retired bootstrap

Worker startup no longer invokes `_bootstrap_group_from_env`. Retained old `NENOY_V2_BOOTSTRAP_GROUP_TITLE` / recent-unwhitelisted variables therefore cannot undo historical or later denials. The historical helper remains callable by old administrative tests, not by the production startup path. `deploy_prepare` is the only normal legacy transition path.

## Rolling old-writer window

Migration 0007 preserves existing denied rows as blocked and pending. It also installs an INSERT-only compatibility default: an omitted whitelist value is represented by NULL until a BEFORE INSERT trigger replaces it with group=true / private=false. The NOT NULL constraint remains. Explicit FALSE is never overridden; no UPDATE trigger exists.

Thus an old web revision can still accept a group's first update after migration and before revision switch without leaving that new group permanently denied. The initial Friends profile is applied only when omitted whitelist and empty profile identify this old-writer INSERT, never on a later update. A PostgreSQL regression compares the SQL defaults to the canonical Python profile to prevent drift.

Application rollback to the previous revision is compatible with the additive columns and INSERT default. It does not restore manual-only group onboarding; that would require an explicit separately reviewed policy rollback.

## Regression checks

Full PostgreSQL CI, including `test_onboarding_rollout_window.py` and startup retained-env regression, is required. The adjusted old startup-order assertion still verifies the transaction boundary before connector preset onboarding; only the retired bootstrap step has been removed. No production credentials or group identifiers are included in tests.
