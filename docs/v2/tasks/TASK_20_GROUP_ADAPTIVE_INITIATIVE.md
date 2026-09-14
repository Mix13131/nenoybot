# TASK 20 — Group Cooldown + Adaptive Initiative

## Goal
Turn on controlled unsolicited participation without letting НеНой dominate a group.

## Starting policy
- base unsolicited cooldown: 12 minutes;
- soft daily limit: 6;
- hard daily limit: 10;
- all three configurable in group profile;
- two recent ignored unsolicited interventions increase cooldown;
- negative feedback reduces initiative faster than positive feedback can raise it;
- positive feedback can increase effective initiative by at most +1 in one policy window;
- recent bot-share guardrail can temporarily block unsolicited participation;
- explicit direct mention/reply bypasses unsolicited cooldown/daily limits;
- mute/shut-up intent sets `chats.silent_until` (default 2 hours) and produces silence.

## Data
Use existing `interventions`, `feedback_events`, `messages`, and `chats` tables. Do not add Redis or a new queue.

An unsolicited Group reply is a Group `interventions.primary_action='reply'` row whose `reason_codes` do not contain direct intent reasons (`direct_mention`, `reply_to_bot`, `question_to_bot`).

## Acceptance
- direct mention works even at hard unsolicited limit;
- two ignored unsolicited interventions increase effective cooldown from 12m to >=18m;
- `заткнись` / explicit mute sets `silent_until` and does not produce a reply;
- positive feedback does not increase initiative sharply (+1 max/window);
- negative feedback decreases initiative faster;
- bot-share guardrail blocks unsolicited, not explicit replies;
- policy values are configurable per group;
- full `tests_v2` green.

## Gate C
After this task the Group MVP is functionally ready for closed testing, but production release still waits for feedback/analytics/reliability/deployment stages.
