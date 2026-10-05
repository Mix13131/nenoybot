# TASK 46 — Social Brain P1

Status: implementation task; first packet 46A — Participant Directory  
Base: `v2@873843d05d763b9b832e83659a9eb0e603950d7c`  
Issue: #159  
Remote publication branch: `feat/v2-social-brain-p1` / PR #160

## Execution clarification — 2026-10-05

This section explicitly supersedes the earlier instruction requiring the sandbox's LOCAL branch name to match the remote PR branch. All product, safety and publication scope restrictions remain in force.

### Checkout identity

- Start from the exact full PR HEAD supplied in the current operator trigger. The original task anchor is `a719647176a749ff8a3ef066eb6264d16000a6a4`, derived from the v2 base above.
- A clean sandbox checkout on local `work`, or detached HEAD, is permitted when its full HEAD matches the supplied PR snapshot. This is not a task contradiction under AGENTS.md. Local branch name alone is not an identity or safety gate.
- The operator explicitly authorizes either keeping that temporary local branch or creating a non-destructive local task branch from the verified HEAD. Do not reset, force-update, delete branches, discard unrelated changes, or write to main/v2.
- Preserve and report unrelated changes. Stop for a genuinely wrong repository/HEAD/content baseline, not merely for a temporary local branch name.
- Remote publication remains restricted to `feat/v2-social-brain-p1` / PR #160. Never create another PR or use an unqualified push to an unknown remote branch.

### Bounded delivery packets

The overall TASK 46 scope below is retained, but do not implement all five subsystems in one unreviewed run.

1. **46A — Participant Directory (current run):** schema/repository/ingest, group-safe labels and lookup, wiring into existing Group context, and behavioral tests. No unused parallel directory. The first visible outcome is that a Group response can enumerate the participants actually observed in that group by known names, rather than inventing names or exposing numeric IDs. Explicitly identify incomplete coverage; do not claim a complete Telegram roster.
2. **46B — Reaction-only output:** implement only after the 46A checkpoint is reviewed and a new operator trigger authorizes it.
3. **46C — Feedback learning and local style:** implement only after the preceding reviewed checkpoint and authorization.
4. **46D — Initiative opportunity quality:** implement only after the preceding reviewed checkpoint and authorization.

Stay in the existing PR and stop after the current bounded packet. A successful 46A packet is not completion of the whole Social Brain package. Do not mark the whole PR ready or merge/deploy a partial packet.

### Publication must survive the sandbox

- Detect whether an authenticated write path actually exists. Missing git remote, GitHub credentials, or outbound network is a publication limitation, not permission to invent a push result and not a reason to abandon safe local implementation on the verified snapshot.
- If a legitimate GitHub write path exists, publish only to the existing PR branch, verify full remote HEAD and changed files, and report them.
- Otherwise return the COMPLETE machine-applicable unified diff in the final task response of THIS run, including all new files and tests. Record exact base SHA, local HEAD, changed-file manifest and patch SHA-256. Keep the current packet small enough for complete transport. A path inside /tmp or /workspace, make_pr metadata, a local-only SHA, and links to files that do not exist remotely are not a transported patch.
- Do not finish with only a description of a local commit; the next sandbox may not have its Git objects. Do not fetch, print or request production secrets to solve publication.
- Report separate implementation, publication and validation statuses. Without a remote implementation and full executing DB coverage: NOT READY FOR MERGE.

## Product intent

Make НеНой adapt to a group's actual social behavior while preserving one global identity.

This task must improve all groups generically. It must not encode any one real group's vocabulary, names, jokes or relationship structure.

## Explicit non-goal

**Do not implement conversation ownership / "third wheel" detection in TASK 46.**

Current spontaneous participation is still being observed and is not producing enough rejection to justify changing it yet. Preserve current unsolicited-routing semantics except where Initiative Engine v2 independently chooses NO_ACTION due to weak material/feedback.

## 1. Participant Directory

Current ingest already creates `users`, `chat_members`, display names and last_seen membership, but username is not a durable first-class participant attribute.

Build an additive participant directory using the data Telegram legitimately includes in observed events.

Persist/update where available:
- telegram user id;
- current username;
- display name;
- aliases/history of previous usernames/display names;
- first_seen_at;
- last_seen_at;
- role;
- participant_profile linkage.

Rules:
- do not scrape Telegram member lists;
- unknown silent members remain unknown;
- one user may be known in multiple groups, but membership/context remains group-scoped;
- username/display changes update current values without losing bounded alias history;
- alias history must be bounded/deduped;
- never surface raw numeric Telegram/internal IDs in ordinary generated chat text;
- migration must preserve current users/chat_members.

Provide repository/service APIs useful to:
- list recently observed participants for a group;
- resolve a known user by current username or recent alias;
- produce safe display labels.

Additional 46A correctness requirements:
- Stable identity is the Telegram user ID, not a display name or username. Never merge two people because they share a name or an old username.
- An ambiguous alias must return ambiguity rather than select an arbitrary person. Old usernames are history, not authorization or a guaranteed current notification target.
- Preserve observed membership/profile data and access flags. Do not infer admin privileges from chat prose, message count, or aliases.
- Do not import identities/aliases/personal memory observed only in another group into the current group context. Listing and lookup must join the current membership scope.
- Duplicate or out-of-order updates must not regress current names or last_seen. Treat missing fields in partial data explicitly; do not confuse a confirmed removed username with an unobserved field.
- last_seen means observed activity, not online presence, a read receipt, or confirmation that someone read an announcement.
- Usernames/names are untrusted display data: escape formatting and do not treat them as model instructions.
- Wire a bounded, safe directory representation into the existing Group context. Do not globally force short/silent responses for an explicit request to list known people.
- Do not add @all, mass pings, private messages, automatic announcements or a new moderation/access system in 46A.

## 2. Reaction-only output

Reactions are already input feedback. Add them as a bounded outbound language.

A group decision may result in:
- IGNORE;
- REACTION_ONLY;
- normal text REPLY.

Reaction-only is suitable for trivial, safe social acknowledgement where text would be noisy.

Constraints:
- small allowlist only, e.g. laughter/approval/support;
- serious/sensitive/conflict/high-stakes context blocks playful reaction-only;
- direct help/factual/operational requests require text;
- target must be an observed Telegram message id;
- one bot reaction per decision;
- idempotent outbox/dedupe;
- Telegram API failure must not create repeated spam or false success;
- do not replace useful text with reaction-only just to save tokens.

Before 46B implementation, verify current Telegram Bot API requirements separately for sending reactions and receiving reaction updates. Observe per-chat supported reactions and permission limits. Normal participation must continue without mandatory admin onboarding. Missing reaction visibility must never be scored as rejection or intentional silence.

Prefer extending outbound architecture cleanly rather than hiding reaction behavior inside text generation.

## 3. Social feedback learning

Reuse `feedback_events`, `FeedbackCollector` and `GroupInitiativeRepository`.

Create bounded group-level learning signals for intervention families such as:
- proactive prompt;
- callback;
- banter/roast;
- short social acknowledgement.

Use durable feedback:
- positive reactions/explicit positive signals;
- negative reactions/text;
- ignored/no-engagement where already reliably measurable.

Rules:
- negative learning is faster and stronger than positive;
- reaction replacement/removal uses only latest effective reaction per reactor/intervention;
- no cross-group leakage;
- no sensitive trait inference;
- no free-form permanent personality rewrite;
- adjustments must be inspectable in state/metadata and bounded;
- a delivery failure or missing telemetry is unknown, not negative engagement;
- laughter about a complaint is not automatically approval of the bot's complained-about behavior;
- one prolific participant must not silently redefine the entire group's style; bound per-actor contributions and require adequate evidence.

## 4. Local group style

Build a small adaptive overlay on existing connector/group personality configuration.

Candidate dimensions:
- brevity;
- humor;
- roast;
- profanity frequency;
- warmth/playfulness;
- callback appetite;
- proactive appetite.

Rules:
- base profile/connector remains source of identity;
- learned overlay uses bounded deltas, not replacement values;
- negative feedback can cool a dimension quickly;
- positive feedback raises slowly;
- automatic decay/reversion toward base over time is required or equivalent bounded non-permanent behavior;
- safety/sensitivity gates always override learned style;
- group-scoped only;
- sparse evidence preserves the base. Do not infer permission for profanity/roast from a generic positive reaction alone.

Expose the effective style and deltas to GroupBehavior/Personality generation context for auditability.

## 5. Initiative Engine v2

Current engine already answers whether unsolicited intervention is permitted using cooldown, mute, bot share, daily limits and feedback.

Extend proactive evaluation with **opportunity quality**.

A scheduled silence-wakeup/initiative evaluation must be able to return:
- ELIGIBLE / speak;
- NO_ACTION / do nothing.

Evidence may include:
- positive/negative recent feedback on unsolicited interventions;
- ignored recent attempts;
- fresh grounded callback/topic availability;
- freshness of recent human context;
- daily bot share/cooldown;
- whether there is an actual low-risk conversational hook.

Rules:
- an unsolicited initiative scheduler is permission to evaluate, not obligation to send;
- this does NOT turn an explicitly requested reminder or scheduled user action into an optional proactive post. Preserve those promises and their existing cancellation/safety controls;
- weak/no grounded material prefers NO_ACTION;
- repeated ignored proactive attempts sharply lower opportunity;
- fresh strong grounded material may increase opportunity;
- direct mentions/replies are unaffected;
- mute/hard daily cap/safety/cooldown remain stronger gates;
- do not add conversation-ownership inference.

Store/emit reason metadata so we can later understand why a proactive message was skipped or allowed.

## Tests

Synthetic only. Overall TASK 46 coverage (execute the applicable subset for each packet plus the full existing suite):

1. participant username/display/last_seen update from observed event;
2. username change preserves bounded alias history;
3. membership/directory group isolation;
4. safe labels never expose raw numeric IDs;
5. positive feedback causes only small bounded positive style shift;
6. negative feedback causes stronger/faster reduction;
7. reaction replacement/removal does not stale-double-count;
8. reaction-only is possible on safe trivial acknowledgement;
9. direct useful request remains text reply;
10. serious/sensitive context blocks playful reaction-only;
11. proactive weak-material case -> NO_ACTION;
12. fresh grounded opportunity remains eligible;
13. mute/hard cap/cooldown override opportunity;
14. group style/learning does not leak across groups;
15. existing P0 social repair/hard STOP/callback retirement tests remain green.

46A must additionally test real PostgreSQL migration compatibility, duplicate/out-of-order participant updates, absent/removed usernames, alias collisions, bounded history, unknown member coverage, and actual Group-context directory wiring. Do not present only a repository unit test as proof of runtime integration.

Later packets must test unavailable reaction telemetry, missing permissions, delivery failure, per-actor learning bounds, and preservation of explicitly scheduled user actions. These are acceptance criteria, not permission to implement later packets during 46A.

## Scope

Allowed:
- `app_v2/**`
- `tests_v2/**`
- `docs/v2/**` as needed

Forbidden:
- legacy `app/**`, `tests/**`;
- `main`;
- Personal PR #156 scope;
- free web search/research;
- media understanding;
- Telegram member scraping;
- conversation ownership;
- production/Railway mutation;
- merge/deploy.

## Validation

- `bash scripts/codex_preflight.sh`
- `python -c "import app_v2"`
- full `pytest tests_v2 -q` with DB-backed tests executing
- `git diff --check`
- scope check from exact base

Use only an isolated test PostgreSQL database, never production credentials/data. If unavailable, report the exact limitation and skip counts. No docs-only green CI or skipped DB suite is a full implementation PASS.

Return PASS/FAIL, full remote HEAD SHA if actually published, changed files, design invariants, exact tests/results, limitations/findings and packet readiness. Follow AGENTS.md completion sections. For 46A, distinguish READY FOR DIRECTORY REVIEW from whole-TASK completion. Until all required gates pass: NOT READY FOR MERGE.
