# TASK 09 — Scene Analyzer

## Status

**IN PROGRESS**

## Goal

Produce compact validated `SceneAnalysis` for Dispatcher using the classifier model only for social/context signals that are not already known deterministically.

## Branch

`task/v2-09-scene-analyzer`

## Scope

- `app_v2/services/scene_analyzer.py`
- `app_v2/prompts/scene_analyzer.md`
- `tests_v2/unit/**`
- this task file

## Requirements

- use `OpenAIAdapter.generate_json(ModelRole.CLASSIFIER, ...)`;
- strict structured output and existing `SceneAnalysis` validation;
- deterministic `direct_mention` and `reply_to_bot` are derived from `EventEnvelope` and must override model output/guessing;
- analyze only compact current event + optional recent-context text supplied by caller;
- output: banter, seriousness, conflict, sensitivity, roast/callback/help/memory opportunities, contradiction, commitment/decision signals, question-to-bot and command intent;
- all scores remain `0..1` through domain validation;
- adapter error, malformed JSON or validation failure returns conservative safe defaults rather than raising into Group runtime;
- conservative fallback must make unsolicited Group behavior quieter: no roast/callback/help opportunity, elevated sensitivity/seriousness;
- no Dispatcher decision inside Scene Analyzer.

## Acceptance fixtures

- [ ] ordinary banter result maps correctly;
- [ ] real conflict maps high seriousness/conflict and low roast;
- [ ] contradiction fixture maps high contradiction;
- [ ] direct mention flag is preserved deterministically;
- [ ] reply-to-bot flag is preserved deterministically;
- [ ] invalid/missing model JSON returns conservative safe fallback;
- [ ] OpenAIAdapter failure returns same fallback;
- [ ] no real API call required in tests.

## Next step

On success: `TASK 10 — Memory Repository + Scope Boundary`.
