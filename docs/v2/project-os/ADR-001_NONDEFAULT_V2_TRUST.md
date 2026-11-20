# ADR-001 — Project OS trust model for NeНой v2 on a non-default branch

**Status:** `PROPOSED / NOT APPROVED`  
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
| D. Explicitly support non-default product base with independently pinned controller and typed dual-branch checks | Retains product workflow; gives auditable separation of source trust and product baseline; reusable for other repos | Requires reviewed controller extension, negative tests and a bootstrap/adoption gate | **RECOMMENDED / PROPOSED** |
| E. Keep manual Project OS G0–G10 only, without deterministic steward | Immediately usable for scoped pilot | Reviewer and human discipline are the only checks; no enforceable SHA or PR gate | **Acceptable TEMPORARY ADVISORY fallback** |

## Proposed model D (not implemented)

**Vocabulary is explicit:** `repository_default_branch=main`, `product_base_branch=v2`, `controller_source=Mix13131/project-os-template@approved_exact_sha`, `target_repo=Mix13131/nenoybot`. Never rename product base to repository default in facts.

### Controller trust

1. Read and execute **only** a separately reviewed controller checkout at an explicitly accepted exact commit SHA. The Project OS template currently has one observed SHA `918bdf656c9914524a9de2724c7347e70b51733c`; that is inspection provenance, **not** automatic owner acceptance.
2. Use a read-only GitHub credential for source verification; do not run any code, policy or GitHub workflow supplied by the unreviewed candidate PR with elevated credentials.
3. If the trusted controller source cannot be validated byte-for-byte against the exact approved SHA, **STOP**. A release/tag name alone is not proof.

### Product trust

1. Independently query GitHub to confirm the full repository identity and actual default branch before trusting product config.
2. Read the exact `v2` product-base SHA twice (before and after PR checks) and require equality. If unavailable, moved, or not a valid commit, **BLOCK**.
3. Load project-specific policy, canon and state only from the exact trusted `v2` BASE SHA; compare to the local checkout. Never substitute the candidate PR's edited governance files as trusted policy.
4. Verify candidate PR target `v2` and expected head SHA, remote head repository identity, local clean HEAD, ancestry, changed-file list, frozen files and action scope. Revalidate any stacked/fork ancestry from exact repository and SHA, not branch names.
5. Explicit governance-path changes (controller, policy, state, scope config or AGENTS) require a separate reviewed governance PR. The tool must not approve changes to its own checking rules.
6. Keep owner G8 acceptance and merge/deploy G9 outside the automatic rule set. Code review is necessary but not sufficient.

### Important limitation

This proposal does not make `v2` a protected branch. A successful read-only controller result is **ADVISORY** until GitHub branch/ruleset enforcement is separately designed, authorized, enabled and verified to block bypasses. Explicit `STEWARD_PASS` or `VERIFIED_CONTEXT` claims before that test are forbidden. `Context Pack v1` must remain unmodified in the canonical Project OS template until an independently reviewed extension is accepted.

## Separate implementation and verification stages

**Stage 1 — Controller/architecture acceptance.** Independent review of this ADR, including source-of-trust bootstrap and GitHub API failure modes; owner chooses D or E. No code.

**Stage 2 — Governance-only Draft PR.** Add scoped policy/state/canon, exact-base checks and negative tests. Work only in separate v2-derived branch/worktree. Do not rewrite existing `AGENTS.md`, enable workflows, touch production or use real chat archives.

**Stage 3 — Run full tests and independent review.** Confirm legitimate case and adversarial GOV-01..12 from G1. Report exact SHA, test command, exit code and repository rule status.

**Stage 4 — Owner gate.** Owner G8 accepts exact SHA and permitted scope. Owner separately decides on merge to v2 and, later, any CI/ruleset activation. Governance bootstrap itself has no runtime deployment.

**Stage 5 — Reflection Coach feature.** Only once governance is validated, take separate consent, topic isolation and group-persona tasks through G2–G9.

## Decision that remains open

- Whether to accept **D** (stronger, reusable, requires a controller extension) or retain **E** (manual/advisory temporarily) while getting user-facing pilot value.
- Exact Controller code location and pinned SHA after review, trust-root bootstrap and treatment of unprotected `v2`.
- Privacy, retention and both participants' opt-in for reflection data.
- Whether Telegram topic-aware storage requires a schema migration (separate risk and approval).

**Current decision:** `PROPOSED`; the target remains `G1 PARTIAL / G4 BLOCKED_FOR_BUILD`.

## Acceptance evidence

This ADR is an authored proposal, not the human approval record. Human acceptance must later name the accepted revision/commit SHA, affected repo and explicit scope. A conversation-level request to proceed with G1 is not consent to install checks, merge, access private group data or deploy services.
