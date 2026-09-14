# TASK 27 — Friends Test Preflight

## Goal
Prepare one approved Telegram group for a controlled НеНой 2.0 friends test without enabling broad rollout.

## Preconditions
- TASK 25 Railway runtime operational.
- TASK 26 Personal smoke functional pass.
- Group whitelist remains mandatory.
- Personal and Group memory scopes remain isolated.
- Unsolicited behavior remains silence-first and adaptive.

## Required checks
1. Confirm target Telegram group id and explicitly whitelist only that group.
2. Confirm bot can receive ordinary group messages, direct mentions, replies and reactions.
3. Confirm bot has enough Telegram permissions to read intended message events.
4. Start with low initiative for Days 1–2.
5. Verify direct mention always works even if unsolicited behavior is muted/cooldown-blocked.
6. Verify ordinary chat can be observed/mapped without automatic reply.
7. Verify Group retrieval cannot read Personal Memory Cards.
8. Verify serious/sensitive scenes suppress roast/callback.
9. Verify `заткнись`/silence request creates temporary group silence.
10. Confirm metrics are available for direct mentions, unsolicited interventions, reactions, negative feedback, mute events and organic participants.

## Suggested initial group profile
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

## Day plan
- Days 1–2: observe/map, low initiative, direct replies normal.
- Days 3–4: medium initiative, cautious callbacks.
- Days 5–7: higher roast/callback if feedback is healthy.

## Blockers before actual TASK 28 start
- rotate Telegram token exposed in private runtime log during first Personal smoke
- choose the exact Telegram friends group
- whitelist only that group

## Exit criteria
- target group explicitly selected
- whitelist record created
- initial group profile set
- one direct mention smoke passes
- one ordinary non-mention message produces no unsolicited reply unless score/initiative gate explicitly allows it
- privacy regression remains green

Do not begin broad unsolicited group behavior outside the selected test group.
