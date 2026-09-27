# TASK 40 — Multi-day Silence Wakeup

## Problem

The original Silence Wakeup policy allowed one proactive attempt per exact human-silence episode forever.

That creates an unintended asymmetry:
- an active group regularly creates new human-message boundaries, so НеНой can wake it again after later silences;
- a truly quiet group can receive one wakeup and then remain permanently ineligible until a human writes again.

This was visible in the second live group `Лучшие ЗДЕСЬ`, where the last observed proactive НеНой activity was 2026-09-23 while the test group continued to get new opportunities.

## New invariant

For an approved group with Silence Wakeup enabled:

- after the configured silence threshold, allow at most **one wakeup attempt per local day for the current human-silence episode**;
- the same continuous silence may therefore receive a new bounded attempt on a later local day;
- a new human message still opens a new silence episode;
- `silence_wakeup_daily_limit` still caps successful wakeups;
- mute, cooldown, negative feedback, soft/hard initiative limits, active hours, timezone and stale-event checks remain unchanged.

## Idempotency

Synthetic event key changes from:

`silence:<scope_id>:<last_human_message_id>`

to:

`silence:<scope_id>:<last_human_message_id>:<YYYY-MM-DD local>`

The repository also reads the latest attempt timestamp so a pre-existing old-format attempt made earlier on the same local day does not cause a duplicate after rollout.

## Product intent

This is a shared behavior fix for all groups, not a special-case for `Лучшие ЗДЕСЬ`.

Character/personality settings can remain group-specific, but proactive long-silence eligibility must be equivalent.
