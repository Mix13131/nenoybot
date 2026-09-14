# TASK 15 — Personal End-to-End v0

## Goal
Wire the first real Personal vertical slice.

## Flow
`claimed private event → EventEnvelope → Scene Analyzer → Personal Dispatcher → Memory Mapper → Personal Memory Retrieval → Personality Engine → Context Builder → Response Generator → intervention audit → outbox`

## Rules
- PersonalPipeline rejects Group scope;
- no direct Telegram send from the pipeline;
- generation failure records the intervention but creates no outbox row;
- outbox dedupe key is stable per event (`reply:<event_id>`);
- explicit remember/forget pass through Memory Mapper;
- Mirror uses callback retrieval;
- Personal memory retrieval stays in Personal scope;
- worker-compatible handler restores the normalized EventEnvelope from persisted event payload.

## Acceptance scenarios
- assistant;
- coach;
- mirror;
- care;
- remember;
- forget;
- callback memory;
- generation failure;
- Group rejected / privacy boundary;
- duplicate event does not create duplicate outbox;
- claimed-event restoration;
- EventWorker-compatible Personal handler.

Do not start TASK 16 before review.