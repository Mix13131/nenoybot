# ADR-001 — Project OS trust model for NeНой v2 on a non-default branch

**Status:** `REWORK v1.1 / NOT APPROVED`  
**Date:** 2026-11-20  
**Owner decision needed:** human project owner, after independent technical review  
**Issue:** [#161](https://github.com/Mix13131/nenoybot/issues/161)  
**Related evidence:** [G1 baseline](G1_BROWNFIELD_BASELINE_2026-11-20.md)  
**Applies to:** Project OS governance for NeНой 2.0 only; `main/app/`, `tests/`, production runtime and other groups remain unchanged.

## Context

The product repository default branch is `main` (legacy v1), while active NeНой v2 product development and PR base are `v2`. Project OS template's current trust preflight deliberately assumes that `policy.base_branch` is GitHub's actual default branch; its PR check independently requires candidate PR base to match policy.

Choosing `base_branch=v2` with stock Context Pack yields `DEFAULT_BRANCH_POLICY_MISMATCH`; choosing `main` yields `UNEXPECTED_BASE_BRANCH` for v2 PRs. These are substantive trust boundaries, not cosmetic configuration inconveniences.

The current `main` and `v2` branch metadata report `protected=false`; automated enforcement is not present in this acceptance evidence.

## Options considered

| Option | Benefits | Costs / failure modes | Result |
| --- | --- | --- | --- |
| A. Move GitHub default to `v2` | Existing Project OS code could keep its invariant | Changes repository-wide workflow and v1/v2 separation; not necessary for scoped feature | **REJECT proposed** |
| B. Put generic Project OS policy `base_branch=v2` into product unchanged | Fast to copy | Fails trusted preflight; hiding mismatch would weaken trust | **REJECT** |
| C. Run target v2 PR through `base_branch=main` | Uses default root | GitHub PR base and local code base disagree; produces misleading verification | **REJECT** |
| D. Explicitly support non-default product base with independently pinned controller and typed dual-branch checks | Retains product workflow; possible long-term verification automation | Requires external trust bootstrap, reviewed controller extension and negative tests | **FUTURE OPTION / PROPOSED** |
| E. Use manual exact-SHA owner/reviewer gates while creating an independently accepted trust checkpoint; no automated Steward yet | Shortest safe route to controlled P0 without rewriting non-default-branch trust code | Requires human evidence discipline; checks remain advisory, not enforced | **RECOMMENDED FOR P0 / PROPOSED** |

## Independent trust bootstrap — required for BOTH options D and E

Codex independent review of prior PR HEAD `88a737485dd8d71812d20fe2acbef2e3d40fc64e` identified a **circular trust blocker (P1)**: stable GitHub `v2` HEAD and policy bytes do **not** prove that the policy was human-accepted. Because `v2` is currently unprotected, a malicious/unapproved governance rewrite can be pushed before a clean read and become self-authoritative.

**Proposed human-controlled trust record (NOT YET CREATED OR ACCEPTED)** must be recorded independently of the unprotected target `v2` source (e.g. signed offline owner record in controlled storage, or another authenticated owner-controlled channel with immutable/exact-version retrieval and reviewer identity). Proposed minimum fields:
- `target_repo_full_name` and immutable GitHub repository ID;
- `repository_default_branch=main` and its exact accepted SHA at checkpoint;
- `product_base_branch=v2` and its exact accepted SHA;
- `controller_repo` and **owner-accepted** controller commit SHA + file/code digest;
- `governance_policy_digest`, accepted governance canon/state digests, version;
- `actor`, `accepted_at`, scope, authorized transition IDs, evidence URL/signature, superseded checkpoint reference.

GitHub file contents, a self-written ADR, an agent comment, or an unprotected base commit are **not sufficient acceptance proof**. On missing, stale, unparseable, unverifiable or unauthorized transition evidence, report `BLOCKED/TRUST_UNKNOWN` **even for advisory automated checks** — not `VERIFIED_CONTEXT`. Legitimate GitHub metadata may still be read as untrusted status evidence. No inference that the observation-only SHA above is accepted.

For manual option **E** during pre-P0 design, cite precise GitHub HEADs, independent review, owner decisions and a separately accepted control record. Do not run/claim a Project OS automated Steward pass. Option **D** becomes a separate future owner-approved project only if this manual path proves insufficient.

## Proposed model D (not implemented)

**Vocabulary is explicit:** `repository_default_branch=main`, `product_base_branch=v2`, `controller_source=Mix13131/project-os-template@approved_exact_sha`, `target_repo=Mix13131/nenoybot`. Never rename product base to repository default in facts.

### Controller trust

1. Read and execute **only** a separately reviewed controller checkout at an explicitly accepted exact commit SHA. The Project OS template currently has one observed SHA `918bdf656c9914524a9de2724c7347e70b51733c`; that is inspection provenance, **not** automatic owner acceptance.
2. Use a read-only GitHub credential for source verification; do not run any code, policy or GitHub workflow supplied by the unreviewed candidate PR with elevated credentials.
3. If the trusted controller source cannot be validated byte-for-byte against the exact approved SHA, **STOP**. A release/tag name alone is not proof.

### Product trust

1. Independently query GitHub to confirm the full repository identity and actual default branch before trusting product config.
2. Query **both** anchors at start and finish: immutable GitHub repo ID, current `default_branch=main`, accepted `main` HEAD SHA, accepted product `v2` HEAD SHA. Reject changed identity, default name or either branch SHA, even if `v2` itself is unchanged. A changed but authorized baseline needs a new accepted checkpoint; no implicit promotion.
3. First compare the policy/canon/state digests from the `v2` BASE against the **independently accepted trust record**. Only then load them as trusted; candidate PR policy, governance code or its own issue text can never bootstrap trust. A mismatch blocks even advisory validation.
4. Verify candidate PR target `v2` and expected head SHA, remote head repository identity, local clean HEAD, ancestry, changed-file list, frozen files and action scope. Revalidate any stacked/fork ancestry from exact repository and SHA, not branch names.
5. Explicit governance-path changes (controller, policy, state, scope config or AGENTS) require a separate reviewed governance PR. The tool must not approve changes to its own checking rules.
6. Keep owner G8 acceptance and merge/deploy G9 outside the automatic rule set. Code review is necessary but not sufficient.

### Important limitation

This proposal does not make `v2` a protected branch. A successful read-only controller result is **ADVISORY** until GitHub branch/ruleset enforcement is separately designed, authorized, enabled and verified to block bypasses. Explicit `STEWARD_PASS` or `VERIFIED_CONTEXT` claims before that test are forbidden. `Context Pack v1` must remain unmodified in the canonical Project OS template until an independently reviewed extension is accepted.

## Separate implementation and verification stages

**Stage 1 — Owner decision on E vs D and independent trust bootstrap.** Independent re-review of this ADR, secure external checkpoint record and default+product-branch TOCTOU negative cases. No new controller code.

**Stage 2 — Conditional governance implementation (only if D explicitly selected).** Add scoped policy/state/canon, external trust record verification, exact-base checks and negative tests in a v2-derived separate Draft PR. If E selected, use documented human/SHA approvals and do not install misleading automated controls. Preserve existing `AGENTS.md` and workflows.

**Stage 3 — Run full tests and independent review.** Confirm legitimate case and adversarial GOV-01..12 from G1. Report exact SHA, test command, exit code and repository rule status.

**Stage 4 — Owner gate.** Owner G8 accepts exact SHA and permitted scope. Owner separately decides on merge to v2 and, later, any CI/ruleset activation. Governance bootstrap itself has no runtime deployment.

**Stage 5 — Reflection Coach feature.** Only once governance is validated, take separate consent, topic isolation and group-persona tasks through G2–G9.

## Decision that remains open

- Whether to approve **E (recommended for P0)** with an independently recorded trust checkpoint; D remains optional later with separate costs/acceptance.
- Exact external trust-record storage/actor/signature policy and accepted controller/governance digests. **None currently recorded as approved**.
- Privacy, retention and both participants' opt-in for reflection data.
- Whether Telegram topic-aware storage requires a schema migration (separate risk and approval).

**Current decision:** `REWORK / PROPOSED E`; the target remains `G1 PARTIAL / G4 BLOCKED_FOR_BUILD` until exact trust record and owner acceptance. No verified Steward.

## Acceptance evidence

This ADR is an authored proposal, not the human approval record. Human acceptance must later name the accepted revision/commit SHA, affected repo and explicit scope. A conversation-level request to proceed with G1 is not consent to install checks, merge, access private group data or deploy services.
