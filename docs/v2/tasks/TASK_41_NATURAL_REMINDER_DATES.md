# TASK 41 — Natural Reminder Date/Context Parsing

## Problem
A normal command such as:

`Завтра в 15:00 напомни @user про встречу в 18:30`

failed because the deterministic parser required exactly one clock expression in the entire message. It could not distinguish the trigger time from a time mentioned inside the reminder content.

Absolute Russian dates such as `28-го сентября в 15:00` were also unsupported.

The same failed reminder command could still be sent through Memory Mapper, producing confusing partial receipts ("memory saved, reminder not scheduled").

## Fix
- Allow one deterministic schedule clock tied to a calendar marker while preserving later contextual times in reminder content.
- Keep explicit alternatives such as `в 15:00 или в 16:00` fail-closed.
- Support Russian absolute dates `28 сентября` / `28-го сентября`, optional year, with timezone resolution.
- If year is omitted and the date has already passed, schedule the next calendar year; same-day past time still fails as past.
- Plain reminder operations do not write long-term memory unless the user explicitly also asks `запомни`.

## Acceptance
- `завтра в 15:00 ... встреча в 18:30` schedules at 15:00.
- `28-го сентября в 15:00 МСК ... встреча в 18:30` schedules correctly.
- target username remains preserved.
- competing trigger times remain rejected.
- existing calendar/reminder regressions remain green.
- reminder-only command does not call Memory Mapper.
