# TASK 10 — Memory Repository + Scope Boundary

## Status

**IN PROGRESS**

## Goal

Implement safe LONG Memory persistence/retrieval with an explicit scope boundary on every operation. Group retrieval must never fall back to Personal memory.

## Branch

`task/v2-10-memory-repository`

## Scope

- `app_v2/repositories/memory_repo.py`
- `app_v2/services/retrieval_engine.py`
- `tests_v2/**`
- this task file

No Memory Mapper, embeddings/vector DB, prompt/model calls, Context Builder or automatic cross-scope transfer.

## Critical rule

Every repository/retrieval operation requires both `scope_type` and `scope_id`. SQL must include those scope predicates. A Group lookup can only return cards with the current Group scope.

## Required behavior

- create MemoryCard + evidence/usage policy JSON;
- update card only inside explicit scope;
- archive/reject/supersede lifecycle updates inside explicit scope;
- get card inside explicit scope;
- link MemoryRelation only inside one explicit scope;
- retrieve active cards with minimum confidence/freshness filters;
- usage-policy filters (`assist`, `callback`, `roast`, `proactive`);
- subject-key overlap relevance;
- basic relation expansion without graph DB;
- callback fatigue: optional exclusion of cards used too recently;
- weighted basic ranking from importance × confidence × freshness plus relevance boosts;
- mark-used updates `last_used_at` only inside explicit scope.

## Acceptance

- [ ] CRUD/lifecycle is scope-safe;
- [ ] usage policy filter prevents roast/callback use when forbidden;
- [ ] confidence/freshness/status filters work;
- [ ] subject-key retrieval works;
- [ ] related-card expansion remains in same scope;
- [ ] callback fatigue excludes recently used card;
- [ ] mandatory privacy regression: Personal card for user cannot be returned from Group retrieval for that same user;
- [ ] no vector DB/graph DB introduced;
- [ ] v1 untouched.

## Next step

On success: `TASK 11 — Memory Mapper`.
