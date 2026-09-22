# TASK 37 — Group Lab importer / anonymizer / frozen replay

Issue: #129

## Цель

Сделать переиспользуемую offline-песочницу для исторических Telegram-экспортов. Первый реальный датасет — многолетняя история «Клуб БРИЛЛИАНТОВЫЙ ГОЛОС», но shared Brain не получает Anna-specific веток.

## Phase A/B — текущий bounded scope

1. Telegram Desktop JSON importer.
2. Детерминированная анонимизация.
3. Удаление raw Telegram/group ids, имён, handles и явных чувствительных контактов/ссылок.
4. Безопасное представление media/service/reactions.
5. Remap message ids с сохранением reply topology.
6. Деление истории на bounded episodes.
7. Frozen replay cases с ограниченным контекстом и пустой ручной меткой:
   - `join`;
   - `stay_silent`;
   - `assist_admin`;
   - `uncertain`.
8. Никаких model calls, production DB writes, Telegram outbox или reminders в A/B.

## CLI

```bash
python -m app_v2.group_lab prepare \
  --input /path/to/result.json \
  --output-dir /path/to/group-lab \
  --owner-name "<admin display name>"
```

Результат:

- `sanitized_history.json`;
- `frozen_replay.json`.

Raw export остаётся вне git.

## Privacy invariants

- source group id не попадает в lab artifact;
- `user...` / `channel...` ids не попадают в lab artifact;
- participant aliases стабильны только внутри данного frozen export;
- exact names заменяются alias; уникальные first-name обращения заменяются, когда это однозначно;
- URLs / Telegram invites / emails / phone-like values / длинные numeric identifiers редактируются;
- filenames не сохраняются;
- reaction `recent` actors отбрасываются;
- real archive не используется в unit tests и не коммитится.

## Phase C — следующий bounded шаг после green A/B

На небольшой размеченной выборке прогнать текущие v2 Brain-компоненты в isolated lab path:

- reply / ignore;
- reason codes;
- intervention score;
- generated response только если Brain решил отвечать;
- никаких live side effects.

Любая неполная parity с production должна быть явно обозначена в отчёте.

## Acceptance A/B

- synthetic unit tests покрывают реальные формы Telegram export;
- byte-stable output при одинаковом input/options;
- deterministic episode boundaries;
- reply topology survives;
- privacy regressions закрыты тестами;
- full `tests_v2` green;
- `main` и legacy `app/` не меняются.

## Phase C status

Isolated behavioral replay is implemented using current v2 Scene Analyzer, Dispatcher, Personality Engine, Response Generator and a selected ConnectorPreset. Replay records reply/ignore, reason codes, intervention score and generated text when applicable. LONG memory/callback retrieval, statement watcher, dynamic cooldown/feedback history, reminders/actions and Telegram outbox remain disabled in the lab and are reported as parity limits.

## Phase C.5 — Group DNA + review pack

Before broad model replay, build a deterministic **operational** map of the group:

- schedule announcements/reminders;
- schedule corrections/changes;
- access/join/link questions;
- material/recording/text questions;
- attendance/availability;
- newcomer joins;
- admin welcomes;
- gratitude/feedback.

No psychological profiling is produced.

Command:

    python -m app_v2.group_lab analyze --history /path/to/group-lab/sanitized_history.json --replay /path/to/group-lab/frozen_replay.json --output-dir /path/to/group-lab --sample-per-category 5

Outputs:

- `group_dna.json` — counts plus sanitized evidence ids;
- `review_pack.json` — a bounded sample spread across the archive.

Run behavioral replay on the review pack first. Do not automatically send the entire multi-year archive through the model.
