# TASK 42 — Birthday Greeting Dedupe

## Problem
A participant can manually prompt НеНой to congratulate a birthday person before the scheduled birthday worker runs. The original yearly `birthday_due` idempotency only deduplicated scheduled birthday events and did not know about an earlier ordinary conversational reply.

Live case:
- birthday self-report saved for Anton on 28 September;
- another participant prompted НеНой to congratulate;
- НеНой already sent `Антон, с днём рождения!` before the 09:00 birthday scan.

Without a secondary guard, the worker could send a duplicate greeting.

## Fix
Before enqueuing `birthday_due`, inspect today's actual generated Group replies in the same scope.
If a reply contains:
- an explicit birthday phrase (`с днём рождения`, `с днем рождения`, `happy birthday`), and
- a token from the birthday participant's display name close to that phrase,

skip the scheduled birthday event for that day.

The durable yearly `birthday_due` idempotency remains the primary mechanism; this is a bounded last-line guard for manual/interrupted flows.

## Acceptance
- today's earlier `Антон, с днём рождения!` suppresses the 09:00 scheduled duplicate;
- no manual greeting -> normal birthday_due still enqueues;
- same-group/date scope only;
- full tests_v2 remain green.
