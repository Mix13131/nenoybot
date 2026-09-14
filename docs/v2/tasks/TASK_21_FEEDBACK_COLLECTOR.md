# TASK 21 — Feedback Collector

## Goal
Persist raw human reactions to НеНой and attach them to the intervention/message that caused them, without mixing raw feedback with derived initiative policy.

## Support
- Telegram emoji reaction added/removed;
- reply to bot;
- organic direct re-mention;
- explicit negative feedback;
- mute/shut-up feedback.

## Delivery correlation
Telegram `sendMessage` result `message_id` must be persisted on the outbox row. Feedback on that Telegram message resolves through outbox payload metadata to the original `intervention_id`.

## Idempotency
Each normalized feedback item has deterministic `feedback_id`; DB enforces unique feedback IDs so event retry cannot duplicate one reaction/update.

## Normalized raw feedback types
- `reaction_positive`
- `reaction_negative`
- `reaction_neutral`
- `reaction_removed`
- `reply_to_bot`
- `organic_remention`
- `explicit_negative`
- `mute`

Raw Telegram reaction/text payload remains in `feedback_events.payload`.

## Acceptance
- reaction to a sent bot message resolves to its intervention;
- outbox records Telegram message id after successful send;
- duplicate collection returns existing feedback instead of inserting twice;
- explicit negative/mute are distinguishable;
- Task 20 initiative policy consumes negative reaction/explicit negative types;
- full `tests_v2` green.
