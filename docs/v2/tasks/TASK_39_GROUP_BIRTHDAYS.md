# TASK 39 — Group Birthdays

## Goal

Make НеНой 2.0 a better long-lived group participant: learn a participant's birthday when Telegram exposes it or when the participant explicitly tells НеНой, then post one personalized group greeting on that date.

This is a bounded group capability, not a global birthday database.

## Product boundary

Sources:
1. Telegram Bot API `getChat(user_id)` -> optional `ChatFullInfo.birthdate`, only when Telegram/privacy makes it visible to the bot.
2. Explicit self-report in the group, addressed to НеНой, e.g.:
   - `НеНой, у меня день рождения 14 мая`
   - `НеНой, мой др — 03.10`

Not accepted automatically:
- third-party birthday claims (`у Лёхи ДР 14 мая`);
- ambient self-disclosure that was not addressed to НеНой;
- inferred age/date.

## Scope / privacy

Birthday state lives in `chat_members.participant_profile`, therefore it is scoped to one group.

Structured fields:
- `birthday.day/month/year?`
- `birthday.source = explicit | telegram_profile`
- `birthday.confirmed`
- `birthday_lookup.checked_at/status`
- `birthday_congratulations.enabled`

Rules:
- an explicit self-report wins over later Telegram profile refreshes;
- if a Telegram-sourced birthday stops being visible, stop using that Telegram-sourced birthday;
- no cross-group propagation;
- birthday year is not copied into the synthetic birthday event;
- generator must not calculate or state age in MVP.

User controls:
- `НеНой, забудь мой день рождения`
- `НеНой, не поздравляй меня с днем рождения`
- `НеНой, поздравляй меня с днем рождения`

## Telegram lookup policy

For human messages in an approved active group:
- explicit self-report/control is handled first;
- otherwise, if no explicit birthday exists and Telegram lookup is stale, call `getChat(user_id)`;
- normal unavailable/not-shared states are cached for 30 days;
- lookup errors retry after 1 day;
- Telegram lookup failure never blocks the normal group conversation.

## Scheduling

No 365 reminder rows.

A bounded birthday worker runs inside the existing `nenoy-v2-worker`:
- scan cadence default: 60 seconds;
- per-group local timezone is mandatory;
- default birthday hour: 09:00 local;
- if worker was unavailable at 09:00, greeting may still fire later that day, but never at/after 21:00;
- group `silent_until` suppresses enqueue until it expires;
- one durable event ID per group/user/local-year:
  `birthday:<scope_id>:<telegram_user_id>:<year>`.

This makes repeated scans idempotent.

## Connector capability

`ConnectorCapabilityProfile.birthdays` controls the feature per connector/group.

Compatibility:
- legacy groups default to enabled unless `birthday_enabled=false`;
- persisted pre-TASK-39 connector payloads that do not contain the new field decode with `birthdays=true`;
- `ConnectorBehaviorProfile.birthday_hour` defaults to 9.

## Birthday event

Synthetic event:
- `event_type=birthday_due`;
- group scope;
- `actor_user_id` is the birthday participant;
- metadata contains display name, day/month/source and `age_allowed=false`.

Dispatcher behavior:
- respects group mute;
- bypasses ordinary cooldown and unsolicited daily limits;
- routes to a Group Generator reply with reason `birthday`.

## Generation

Birthday greeting uses:
- group personality;
- participant adaptation;
- group-scope relevant memories for the birthday participant.

Rules:
- 1–3 lively sentences;
- no system/scheduler/database wording;
- no invented memories;
- no age;
- no sensitive memories (health, money, relationship/family problems, fears/vulnerabilities);
- light roast/callback only when grounded and friendly.

## Acceptance

Automated:
- Bot API visible birthdate parsed;
- missing/private birthdate is a normal state;
- direct self birthday saves structured state;
- ambient/third-party birthday does not save;
- user can clear and disable/enable greetings;
- explicit birthday is not overwritten by Telegram refresh;
- Telegram-sourced birthday is dropped when no longer visible;
- scanner respects timezone/hour/mute/capability;
- birthday event is idempotent in PostgreSQL;
- birthday due bypasses scene analysis and normal cooldown;
- group mute blocks birthday due;
- connector backwards compatibility remains green;
- full `tests_v2` green.

Manual live acceptance:
1. In a controlled group, send `НеНой, у меня день рождения <today>`.
2. Verify profile state is group-scoped and no generic Memory Card is required.
3. Trigger/run birthday scan after configured local hour.
4. Verify exactly one `birthday_due` event and one greeting.
5. Re-run scan -> no duplicate.
6. Send `НеНой, не поздравляй меня с днем рождения`; verify no future greeting.
7. Verify ordinary group member messages remain unaffected if Telegram `getChat` cannot expose birthday.
