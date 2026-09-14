# TASK 12 — Personality Engine

## Goal
Compute final effective personality deterministically from scope profile, participant adaptation, response mode, temporary overrides and scene context.

## Required pipeline
`Core → Context Profile → Participant Adaptation → Mode Modifiers → Situational Override → clamp 0..10`

## Rules
- no LLM calls;
- `profanity_level` and `profanity_frequency` are independent;
- group profanity ceilings cannot be exceeded by participant/temporary requests;
- Personal Care reduces pressure/challenge and increases warmth/care;
- serious/conflict scenes suppress roast/humor and increase sensitivity;
- sensitive scenes prefer care over pressure;
- all final values stay in 0..10.

## Acceptance
Table-driven/unit coverage for defaults, mode modifiers, adaptation, seriousness, sensitivity, profanity ceilings, independent profanity axes and clamping.

Do not start TASK 13 before review.