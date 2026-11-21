# G1 — Brownfield baseline and Project OS compatibility review

**Date:** 2026-11-20
**Issue:** [#161](https://github.com/Mix13131/nenoybot/issues/161)
**Status:** `REWORK v1.1 / PARTIAL — NOT VERIFIED_CONTEXT`
**Product:** НеНой v2 · proposed `reflection_coach_v1`
**Scope of this change:** documentation-only technical review; no governance runtime, code, CI or production changes
**Product source:** `Mix13131/nenoybot@v2` — GitHub SHA `805d2aafc709cb2323f228d791b16bb1f63f64b8`
**Legacy source:** `Mix13131/nenoybot@main` — GitHub SHA `37383b9572e950b54bb759ee6f4f464cd714a0af`
**Project OS source:** `Mix13131/project-os-template@main` — GitHub SHA `918bdf656c9914524a9de2724c7347e70b51733c`
**Source confidence:** GitHub API remote inspection only. These SHAs are snapshots, not permanent approvals; refresh at the beginning of every work session.

## 1. Why this review exists

The owner wants Project OS G0–G10 to control a bounded Reflection Coach feature in the existing НеНой v2 project. Adopt Project OS as a **brownfield overlay**, not as a new repository, not as a replacement of current product instructions, and not as an autonomous authority.

Current product branch design differs from Project OS template assumptions:
- GitHub repository **default branch is `main`**, containing legacy НеНой v1.
- NeНой 2.0 is developed and deployed from **non-default `v2`**.
- Proposed feature PRs should target `v2` and preserve `app/`, `tests/`, and current `AGENTS.md`.
- Both `main` and `v2` were reported **unprotected** by GitHub branch metadata at this checkpoint. This prevents claims that checks are enforceable.

## 2. Evidence inventory (read-only)

| Component | Observed evidence | Consequence |
| --- | --- | --- |
| Product ownership | `AGENTS.md` describes separate legacy `main` and `app_v2/` in `v2`; forbids unsolicited refactors, new infrastructure and direct production changes | Use isolated `v2`-derived PR and keep legacy untouched |
| Work in flight | Open PRs against `v2`: #95, #142, #143, #144, #145, #146, #147, #150, #156 (observed 2026-11-20) | Re-check live heads and check overlap before each change |
| GitHub default branch | `main@37383b9...` observed; branch-protected=false | May not assume Project OS trust root is protected |
| Product base | `v2@805d2aa...` observed; branch-protected=false | **Unknown policy provenance blocks even advisory trust claims** until independently accepted owner checkpoint; later passing checks remain ADVISORY without enforcement |
| Project OS canon | `.project-os/METHOD.md` defines G0–G10, human G8, explicit G9 and read-only trusted controller | No self-approval; CI PASS is not release authorization |
| Context Pack v1 | `scripts/project_steward/context_pack.py` compares `policy.base_branch` against GitHub `default_branch` | Policy `base_branch=v2` fails `DEFAULT_BRANCH_POLICY_MISMATCH` |
| Context Pack v2 | `scripts/project_steward/context_pack_v2.py` repeats this check in preflight **before** open PR detail reads | Config-only workaround is not valid |
| Steward PR check | `scripts/project_steward/check.py` requires `facts.base_ref == policy.base_branch` and exact git base/head/diff identity | Policy `base_branch=main` cannot pass `v2`-based product PR |
| Project OS target | `nenoybot@v2` has no `.project-os/` directory | Actual Project OS adoption has **not** happened |
| Telegram topics | `telegram_webhook.py` captures `message_thread_id`; `context_builder.py` notes message table lacks persisted thread ID and skips threaded mapper context | Reflection Coach weekly reviews require separate topic isolation design |
| Runtime | Existing Railway `nenoy-v2` has web, worker and PostgreSQL; historical successful deployments are not Reflection Coach acceptance | No runtime or production claim from this review |

Relevant source links:
- [Project OS METHOD](https://github.com/Mix13131/project-os-template/blob/main/.project-os/METHOD.md)
- [Context Pack v1](https://github.com/Mix13131/project-os-template/blob/main/scripts/project_steward/context_pack.py)
- [Context Pack v2](https://github.com/Mix13131/project-os-template/blob/main/scripts/project_steward/context_pack_v2.py)
- [Steward check](https://github.com/Mix13131/project-os-template/blob/main/scripts/project_steward/check.py)
- [НеНой AGENTS](https://github.com/Mix13131/nenoybot/blob/v2/AGENTS.md)
- [НеНой product roadmap](https://github.com/Mix13131/nenoybot/blob/v2/docs/v2/ROADMAP.md)
- [Telegram normalization](https://github.com/Mix13131/nenoybot/blob/v2/app_v2/adapters/telegram_webhook.py)
- [Threaded-context fail-closed note](https://github.com/Mix13131/nenoybot/blob/v2/app_v2/services/context_builder.py)

## 3. Decision: do not pretend the current Controller supports non-default product base

**Rejected shortcut A:** copy stock Project OS to `v2` and set `policy.base_branch = "v2"`. The controller checks GitHub default branch and must block it.

**Rejected shortcut B:** set `policy.base_branch = "main"` and target Reflection Coach PRs to `v2`. PR facts and local base/ref checks would disagree.

**Rejected shortcut C:** switch GitHub default branch to `v2`, modify legacy `main`, or disable identity checks merely to obtain PASS. These change accepted product boundaries or weaken governance.

**Recommended near-term route (proposed ADR-001 v1.1):** preserve existing product branches and use **manual Project OS G0–G10 controls with exact-SHA owner/reviewer records (option E)**. A full non-default-branch adapter (option D) is deferred unless independently justified. Both choices require an external, independently accepted trust bootstrap; do not mistake stable GitHub metadata for governance approval.

**If a future option D is specifically authorized**, its controller must have these properties:
1. **Controller trust:** run only a trusted, reviewed and SHA-pinned Project OS implementation; never execute controller code or policy from the candidate PR with privileged credentials.
2. **Product trust:** compare candidate policy/canon/state against an independently owner-accepted **checkpoint and digests outside the unprotected v2 tree**; verify repo identity and `main` AND `v2` branch identities/SHAs both before and after reading PR metadata. Any mismatch or missing external acceptance must block.
3. The exception is **not** a blanket waiver for `DEFAULT_BRANCH_POLICY_MISMATCH`; it is a new typed contract distinguishing `repository_default_branch` from `product_base_branch` and rejecting unexpected changes to either.
4. Until the trust checkpoint is accepted and verified, return `BLOCKED/TRUST_UNKNOWN` even for advisory controller validation. After the accepted checkpoint but before enforceable branch protection, only report `ADVISORY`, never `VERIFIED_CONTEXT` or `STEWARD_PASS`.

This route requires implementation and dedicated negative tests. **Neither the adapter nor its verification exists in this PR.**

## 4. Proposed Project OS adoption order

1. **G0 + G1:** owner reviews proposed Reflection Coach scope, consent/privacy boundary and this baseline; unresolved decisions remain open. Current result: `G1 PARTIAL`, because no trusted local Context Pack was run against the target and no acceptance exists.
2. **Independent architecture/Controller review:** revise ADR-001 after Codex P1 circular-trust and P2 dual-branch race findings; owner chooses manual E vs optional automated D, plus accepts an external checkpoint record. Do not modify `main` as workaround.
3. **Conditional governance-only implementation:** only if owner chooses D, create a separate task for adapter/policy/canon with independent trust bootstrap. Under manual E use reviewer + exact-SHA gates; do not pretend the automated Project OS is installed. No auto CI or branch protection.
4. **G6 + G7:** run deterministic and adversarial tests, verify exact PR HEAD and SOURCE, obtain independent review; Project Steward remains `ADVISORY` without enforced GitHub branch protection.
5. **G8 + G9:** owner approval is bound to a reviewed SHA; merge and deploy require separate explicit permissions. Governance-only adoption is not production deployment.
6. **G10:** record accepted branch baseline, policy, decision register, recovery and known blockers; ensure fresh agent can resume without old chat.
7. **Only after controls are validated:** create separate Reflection Coach feature tasks for forum topic isolation, consent/privacy, scoped persona, grounded weekly review, and 7-day bounded pilot.

## 5. Mandatory negative/positive security tests for the future adapter

| ID | Test | Required result |
| --- | --- | --- |
| GOV-01 | Legitimate `v2` PR with trusted controller, reviewed BASE policy and exact fetched SHA | Pass *advisory* subject to tests and human gates |
| GOV-02 | Merely set template `policy.base_branch=v2` without adapter | Hard BLOCKED, `DEFAULT_BRANCH_POLICY_MISMATCH` |
| GOV-03 | Candidate PR changes its own policy/controller/state and tries to self-approve | BLOCKED / independent governance review |
| GOV-04 | GitHub default branch unexpectedly changed from `main` before OR **during** the read, while `v2` stays unchanged | BLOCKED; verify both anchors at final step |
| GOV-05 | `v2` base moved between first and last verification | BLOCKED with race evidence |
| GOV-06 | Candidate PR HEAD changed after independent review/owner decision | Review/acceptance invalidated; re-run |
| GOV-07 | Fork PR or stacked-parent text mimics trusted ref but repo identity/SHA is wrong | BLOCKED; no implicit ancestry trust |
| GOV-08 | Dirty or nonmatching local checkout, stale local base or mismatched trusted policy bytes | BLOCKED before privileged candidate reads |
| GOV-09 | GitHub API fails, pagination incomplete, or scoped PR count truncated | UNKNOWN/BLOCKED, never exhaustive/PASS |
| GOV-10 | Branch rules inactive / `v2` unprotected | `ADVISORY`; never claim GitHub enforcement |
| GOV-11 | Existing v1/v2 product code or unrelated PR paths changed | BLOCKED_FOR_SCOPE |
| GOV-12 | Real chat identifiers/secrets/Telegram export in PR | BLOCKED_FOR_PRIVACY |
| GOV-13 | Unapproved attacker changes policy/canon in unprotected `v2` before initial GitHub read | BLOCKED: digest not in independently accepted checkpoint; stable HEAD alone insufficient |
| GOV-14 | Accepted checkpoint missing/expired or supplied only inside untrusted candidate | BLOCKED/TRUST_UNKNOWN, including advisory validation |
| GOV-15 | `main` HEAD changes only between first and last reads, `v2` unchanged | BLOCKED; do not issue result from mixed-state snapshot |

## 6. G1 blockers, status and next safe operation

- **BLOCKER 1:** Generic Project OS Context Pack cannot validate a non-default product branch as currently implemented. Interim manual E is proposed but no independent accepted owner checkpoint exists.
- **BLOCKER 2:** Neither GitHub `main` nor `v2` currently has verified branch enforcement.
- **BLOCKER 3:** No independently reviewed, product-specific `.project-os/` canon/policy/state exists in `v2`.
- **BLOCKER 4:** Historical context for Telegram forum topics is not proven isolated.
- **BLOCKER 5:** G0/G2 product and privacy contracts are still PROPOSED; each participant's informed opt-in for group analysis must be captured before live processing.

**G1 result:** `REWORK / PARTIAL`. No auto validation may claim `ADVISORY TRUSTED` without external checkpoint; manual status observations remain nonauthoritative.
**G4 build authorization:** `BLOCKED_FOR_BUILD` until required owner choices and trust contract exist.
**What was actually checked:** GitHub repository/branch metadata, target AGENTS/source, canonical Project OS code and open PR metadata. This document does **not** claim a test run, local checkout audit, independent reviewer approval, G7 PASS, owner G8 acceptance, merge or deployment.

**Exactly one next safe operation:** independent re-review of proposed ADR-001 v1.1 (manual E plus external trust record, dual-anchor recheck), then present exact SHA and checkpoint contract to owner for decision. Keep production untouched.
