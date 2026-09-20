# TASK 27 — Friends Test Preflight

**Status: PASS / COMPLETED — 2026-09-20**

## Goal
Prepare one approved Telegram group for a controlled НеНой 2.0 Friends Test without enabling broad rollout.

## Exit evidence

Preflight закрыт на основании уже выполненных live-проверок и текущего зелёного regression suite:

- 2026-09-14 явно выбрана одна controlled test group и активирована через whitelist;
- стартовый friends-profile: low initiative (`initiative=3`) с humor/sarcasm/roast/callback/sensitivity настройками;
- ordinary non-mention group message дошёл до v2 и не вызвал unsolicited reply — silence-first path подтверждён live;
- direct mention дошёл до v2 и получил ответ — explicit path подтверждён live;
- ordinary group traffic был доставлен Telegram webhook, поэтому observation path функционально работает независимо от того, каким конкретно BotFather/admin setting это обеспечено;
- post-token-rotation webhook recovery закрыт merged PR #84;
- current test suite сохраняет Group/Personal privacy boundary: Group retrieval/context запрашивают только Group scope;
- serious/sensitive scene tests подавляют roast/callback escalation;
- `заткнись` / mute request path создаёт temporary group silence; human-to-human `заткнись` не мутит группу;
- reactions/negative feedback/reaction removal имеют scoped semantics и idempotency;
- analytics report включает direct mentions, unsolicited interventions, organic participants, reactions, negative feedback и mute events;
- финальная production/code точка после последующего hardening: `v2@0fe8c260761c446aca9a3fdfd3ae1b00c6a8f05b`, full CI **494 passed, 1 warning**.

## Initial profile used for controlled preflight

```json
{
  "profile": "friends",
  "unsolicited_enabled": true,
  "initiative": 3,
  "humor": 8,
  "sarcasm": 8,
  "roast": 7,
  "callback": 8,
  "profanity_level": 6,
  "profanity_frequency": 3,
  "sensitivity": 8,
  "callback_fatigue_minutes": 180
}
```

## What this completion means

TASK 27 proves that one explicitly selected group can safely enter the product experiment.

It does **not** mean:
- full Friends Test is complete;
- broad rollout is allowed;
- unknown groups may auto-whitelist;
- all personality thresholds are validated.

## Next

TASK 28 / Phase 9 — **7-day Controlled Friends Test**:
- Days 1–2 low initiative;
- Days 3–4 medium initiative + cautious callbacks;
- Days 5–7 stronger character only if feedback remains healthy.

Primary product signal: participants other than the owner begin addressing НеНой organically and repeatedly.
