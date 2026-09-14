# TASK 07 — Deterministic Dispatcher Policy

## Status

**IN PROGRESS**

## Goal

Implement the policy decisions from `DISPATCHER_SPEC.md` that do not require an LLM. The service must return an explainable `DispatcherDecision` and preserve the principle that Group Mode is silent by default.

## Branch

`task/v2-07-deterministic-dispatcher`

## Scope

- `app_v2/services/dispatcher.py`
- non-breaking additions to `app_v2/domain/enums.py` if required for stable reason codes
- `tests_v2/unit/**`
- this task file

No Scene Analyzer model calls, OpenAI adapter, Memory retrieval, Response Generator or Telegram delivery.

## Inputs

The deterministic policy may consume:

- `EventEnvelope`;
- existing `SceneAnalysis` signals;
- explicit policy state: mute/silent flag, cooldown, unsolicited count, limits, group initiative level, recent bot activity, running-joke/broken-commitment hints.

## Required behavior

- Personal message defaults to `reply` with a Personal response mode candidate;
- explicit direct mention / reply-to-bot / question-to-bot in Group returns `reply` even when unsolicited cooldown is active;
- group mute/silence blocks unsolicited interventions;
- hard daily limit blocks unsolicited interventions;
- active cooldown blocks unsolicited interventions;
- ordinary group message with no strong signal returns `ignore`;
- serious/sensitive context cannot select roast;
- Intervention Score uses documented deterministic weights and is clamped `0..100`;
- threshold: `<60` ignore; `60..74` only high initiative may reply; `>=75` reply unless blocked;
- stable reason codes and policy version included in metadata;
- no LLM invocation.

## Acceptance scenarios

- [ ] direct mention replies despite cooldown;
- [ ] reply-to-bot replies despite cooldown;
- [ ] ordinary group message -> ignore;
- [ ] group muted -> ignore;
- [ ] hard daily limit -> ignore unsolicited;
- [ ] serious context blocks roast even with high roast opportunity;
- [ ] callback can win mode selection when strong and allowed;
- [ ] high roast scene selects group roast when safe;
- [ ] Personal event returns reply candidate;
- [ ] score/reason codes deterministic and test-covered.

## Next step

On success: `TASK 08 — OpenAI Adapter + Model Router + Usage Capture`.
