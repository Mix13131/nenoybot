# TASK 45 — Group humanity P0

Status: implementation task  
Base: `v2@d85cc877a5a845a1899a459ac4b7b9fa047600e1`  
Issue: #157  
Branch: `fix/v2-group-humanity-p0`

## Goal

Make v2 Group behavior more human across **all groups** without copying the tone, names, jokes, or private details of any one real chat.

The task addresses five universal failure modes found during real-history review:

1. stop/cancel must actually stop;
2. criticism should trigger repair, not escalation;
3. callbacks/topics must fatigue and retire;
4. group replies must be able to be short or silent;
5. the bot must not claim capabilities/evidence it did not use.

This is a behavior/invariant task, not a personality rewrite.

## 1. Deterministic hard STOP

Explicit operational stop intent must not depend on the LLM.

Examples of semantic intent to cover with synthetic test phrases:
- stop;
- enough;
- stop reminding;
- do not remind again;
- stop this recurring reminder;
- silence this until tomorrow;
- equivalent clear Russian forms.

Requirements:
- cancel only the relevant action when the target can be resolved;
- do not globally mute the group unless the user clearly asks for group-wide silence;
- persist cancellation durably;
- a queued or already-claimed scheduled action must re-check cancellation immediately before outbound send;
- once stop is confirmed, no later outbound item for that cancelled action may be sent;
- retries must not resurrect cancelled actions.

## 2. Social repair

Add a bounded repair signal for direct criticism such as:
- "you're overdoing it";
- "be useful instead";
- "stop attacking";
- "you're creating conflict";
- clear equivalents.

Expected behavior:
- acknowledge briefly;
- reduce adversarial/roast posture for that turn;
- pivot to the requested useful task where one exists;
- do not counterattack the participant;
- do not permanently flatten the character after one repair event.

Prefer an explicit scene/decision signal or deterministic pre-classifier over a prompt-only patch.

## 3. Callback/topic fatigue

Current memory/callback retrieval must gain lifecycle control.

Minimum behavior:
- repeated use increases fatigue;
- recent reuse suppresses selection;
- explicit negative feedback can retire/suppress a callback/topic;
- a retired joke/callback is distinct from deleting the underlying factual memory;
- proactive selection should prefer fresh material over recently exhausted material;
- state is group-scoped and does not leak across groups.

If schema changes are needed, use an additive migration.

## 4. Brevity and silence

Introduce a bounded group response intent/size control. It does not need to be a new public domain object if an existing mechanism can cleanly carry it.

Supported outcomes should include at least:
- SILENCE
- REACTION/MICRO equivalent
- SHORT
- NORMAL

Rules:
- trivial acknowledgements and banter turns should not always become 20–40 word punchlines;
- direct factual/help requests must still receive useful answers;
- explicit direct mentions are not automatically permission for a long monologue;
- unsolicited proactive messages should remain conservative.

Tests should validate behavior structurally where exact wording would be brittle.

## 5. Capability honesty

Generation/context must not state that the bot:
- checked fresh reviews;
- researched current information;
- opened/read a page;
- verified a live fact;
- contacted a service;

unless evidence for that capability exists in the current action/tool context.

When unavailable:
- say the limitation plainly;
- suggest the concrete next input/action needed;
- do not fabricate certainty.

Prefer a capability/evidence contract passed into generation over a growing list of banned phrases.

## Regression scenarios

Use only synthetic fixtures. Never add real group names, participant names, usernames, IDs, screenshots, or verbatim private-chat material.

Required:
1. recurring group reminder exists -> explicit stop -> future send suppressed;
2. item already claimed -> stop occurs -> pre-send guard suppresses send;
3. retry after cancellation cannot resurrect it;
4. callback used repeatedly -> fatigue prevents immediate reuse;
5. explicit "stop this topic" -> callback retired but factual memory remains available where appropriate;
6. criticism/repair turn -> no adversarial escalation;
7. low-value acknowledgement -> short/silent outcome allowed;
8. direct help request -> useful normal answer preserved;
9. no fabricated current-review/research claim without evidence;
10. group isolation for fatigue/repair state.

## Scope

Allowed:
- `app_v2/**`
- `tests_v2/**`
- `docs/v2/**` only as needed for this task

Forbidden:
- legacy `app/**`
- legacy `tests/**`
- `main`
- production/Railway changes
- merge
- work owned by PR #156 (Personal actions/reminders)
- hardcoded behavior for a specific real group

## Engineering constraints

- Reuse GroupPipeline, dispatcher, GroupBehaviorEngine, reminder/scheduled-action repositories, feedback/memory structures where sensible.
- Invariants over prompt-only behavior.
- Preserve idempotency and group/user isolation.
- No manual DB patch as the product solution.
- No hidden environment flag required for normal operation.
- Additive migrations only.

## Validation

Run:
- `bash scripts/codex_preflight.sh`
- `python -c "import app_v2"`
- full `pytest tests_v2 -q` with isolated PostgreSQL for DB-backed cases
- `git diff --check`
- scope check against base SHA

No skipped PostgreSQL tests may be presented as full DB PASS.

## Required Codex report

Return:
1. status PASS/FAIL;
2. exact full HEAD SHA;
3. changed files;
4. implemented invariants/design;
5. tests added and what each proves;
6. exact full test result;
7. known limitations / remaining findings;
8. readiness: NOT READY / READY FOR REVIEW.

Do not merge or deploy.
