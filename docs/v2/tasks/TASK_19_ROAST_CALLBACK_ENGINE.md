# TASK 19 — Roast + Callback + Running Joke Engine

## Goal
Enable evidence-backed Friends Group behavior without random one-liners. Every unsolicited roast/callback must have a concrete scene/memory reason and pass safety/tolerance gates.

## Core rules
- unsolicited participation is controlled by group profile feature flag `unsolicited_enabled`;
- callback probe retrieves only current Group scope with callback-fatigue filter;
- high-confidence relevant `running_joke`, `pattern`, `contradiction`, or broken `commitment` can strengthen callback opportunity;
- roast requires group roast setting > 0, safe scene, and participant tolerance;
- participant negative adaptation can suppress roast entirely;
- group profanity level/frequency come from group profile and remain separate;
- serious/sensitive/conflict scenes suppress roast/profanity through Personality Engine;
- no memory -> no fabricated callback.

## Acceptance scenarios
- `уже еду` running-joke memory selects Group Callback;
- contradiction / переобувание can select grounded callback/roast;
- broken promise contributes Dispatcher reason;
- recently-used running joke is filtered and cannot force callback;
- `profanity_level=0` remains zero;
- high profanity in serious scene is suppressed;
- low/negative roast tolerance softens or blocks roast;
- unsolicited feature flag OFF preserves silence-first behavior;
- Personal memory never enters Group probe/context.
