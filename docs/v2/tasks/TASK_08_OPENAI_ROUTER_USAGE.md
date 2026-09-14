# TASK 08 — OpenAI Adapter + Model Router + Usage Capture

## Status

**IN PROGRESS**

## Goal

Create the single v2 boundary for OpenAI Responses API calls, configure task-based model routing, capture usage/cost for both successful and failed calls, and support strict JSON-schema outputs without allowing business services to hardcode model names.

## Branch

`task/v2-08-openai-router-usage`

## Scope

- `app_v2/adapters/openai_adapter.py`
- `app_v2/services/model_router.py`
- `app_v2/services/cost_tracker.py`
- `app_v2/repositories/usage_repo.py`
- `app_v2/config.py`
- `.env.example`
- `tests_v2/unit/**`
- this task file

No Scene Analyzer prompt/service yet. No Memory Mapper, Response Generator or live production calls.

## Logical model roles

- `MODEL_CLASSIFIER`
- `MODEL_MEMORY`
- `MODEL_GENERATOR`
- `MODEL_DEEP`

Concrete model IDs live in config only. Initial defaults (reviewed against OpenAI docs on 2026-09-14):

- classifier: `gpt-5.6-luna`
- memory: `gpt-5.6-luna`
- generator: `gpt-5.6-terra`
- deep: `gpt-5.6-sol`

## Requirements

- OpenAI client instantiated only inside adapter;
- API key from `NENOY_V2_OPENAI_API_KEY` or explicit test injection;
- clear missing-key configuration error;
- task-based model routing and reasoning effort;
- Responses API text output;
- strict JSON Schema output via Responses `text.format=json_schema`;
- explicit timeout;
- no blind application-level retry in TASK 08;
- capture input/output/cached tokens, latency, model and success;
- failed call also writes a usage record with zero/known tokens and `success=false`;
- estimated cost uses a versioned pricing table and handles cached input separately;
- business services do not contain concrete model names.

## Acceptance

- [ ] router returns configured model for all four roles;
- [ ] fake Responses client text call succeeds;
- [ ] structured call sends strict JSON-schema format and parses JSON;
- [ ] successful call creates usage record;
- [ ] failed call creates failed usage record and re-raises useful error;
- [ ] cached token cost math covered;
- [ ] no real API key/call required by tests;
- [ ] existing v2 tests remain compatible.

## Next step

On success: `TASK 09 — Scene Analyzer`.
