# TASK 11 — Memory Mapper

## Goal
Transform raw `EventEnvelope` messages into compact, evidence-backed Memory Cards without uncontrolled growth.

## Scope
Allowed:
- `app_v2/services/memory_mapper.py`
- `app_v2/prompts/memory_mapper.md`
- memory-repository helper logic required for semantic merge
- `tests_v2/unit/**`

Do not implement Personality Engine, Context Builder, reminders, or response generation.

## Product rules
1. Explicit `запомни:` is deterministic and does not require a model guess.
2. Explicit `забудь это` archives only memory IDs explicitly supplied by the caller and only in the current scope.
3. Model inference must contain evidence and confidence is capped at `0.70` before confirmation.
4. Semantic duplicates update one logical card rather than creating endless cards.
5. Pattern promotion requires at least 3 evidence points across at least 2 independent episodes.
6. Mapper failure must never break a direct reply pipeline.
7. Scope is always explicit; Personal and Group never mix.

## MVP memory types
`goal`, `project`, `commitment`, `decision`, `plan`, `event`, `preference`, `observation`, `contradiction`, `quote`, `running_joke`, `pattern`.

## Acceptance
- explicit remember test;
- explicit forget test;
- repeated commitment semantic merge test;
- inferred confidence cap test;
- pattern threshold test;
- model failure safe-noop test;
- scope-preservation test.

After completion stop. Do not start TASK 12 automatically.