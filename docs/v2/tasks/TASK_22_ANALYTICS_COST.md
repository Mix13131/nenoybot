# TASK 22 — Analytics + Cost Dashboard Queries

## Goal
Provide SQL-backed product health and COGS reports without adding a web dashboard or external BI.

## Personal metrics
- messages;
- replies;
- proactive replies;
- memory writes;
- grounded callbacks;
- LLM cost.

## Group metrics
- participant count;
- organic participants who independently address/reply to НеНой;
- direct mentions;
- unsolicited interventions;
- reaction rate;
- roast hit rate;
- ignored interventions;
- negative feedback;
- mute events;
- LLM cost per group/day.

## Rules
- date window is explicit and timezone-aware;
- optional `scope_id` narrows the report;
- no external BI/UI;
- CLI can emit human text or JSON;
- ratios return 0 when denominator is zero;
- analytics are read-only.

## Acceptance
- fixture/fake repository tests cover formulas and zero denominators;
- repository SQL is scope-aware;
- CLI/report serialization is stable;
- full `tests_v2` green.
