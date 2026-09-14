# TASK 13 — Context Builder

## Goal
Build a compact generation package instead of sending raw history to the model.

## Inputs
- current `EventEnvelope`;
- `SceneAnalysis`;
- `DispatcherDecision`;
- effective `PersonalityState`;
- HOT messages from current scope;
- relevant LONG Memory from current scope;
- optional action/task state.

## Guardrails
- HOT: max 100 messages and about 4000 estimated tokens;
- LONG: max 8 cards and about 1200 estimated tokens;
- retrieval always receives explicit `scope_type + scope_id`;
- evidence excerpts remain attached for grounded callbacks;
- no Personal -> Group fallback;
- no tokenizer dependency required for MVP; conservative deterministic estimation is acceptable.

## Acceptance
- oversized chat is bounded;
- memory count/token budget is bounded;
- privacy regression confirms Group requests only Group scope;
- evidence preserved;
- decision/personality/target/action state included.

Do not start TASK 14 before review.