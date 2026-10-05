# TASK 46 — Social Brain P1

Status: implementation task  
Base: `v2@873843d05d763b9b832e83659a9eb0e603950d7c`  
Issue: #159  
Branch: `feat/v2-social-brain-p1`

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
- adjustments must be inspectable in state/metadata and bounded.

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
- group-scoped only.

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
- scheduler is permission to evaluate, not obligation to send;
- weak/no grounded material prefers NO_ACTION;
- repeated ignored proactive attempts sharply lower opportunity;
- fresh strong grounded material may increase opportunity;
- direct mentions/replies are unaffected;
- mute/hard daily cap/safety/cooldown remain stronger gates;
- do not add conversation-ownership inference.

Store/emit reason metadata so we can later understand why a proactive message was skipped or allowed.

## Tests

Synthetic only. Required coverage:

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

Return PASS/FAIL, full remote HEAD SHA, changed files, design invariants, exact tests/results, limitations/findings and READY FOR REVIEW / NOT READY.
