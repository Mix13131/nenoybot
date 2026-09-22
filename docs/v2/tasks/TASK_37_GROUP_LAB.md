# TASK 37 — Group Lab / historical community replay

Issue: #129

## Цель

Сделать переиспользуемую offline-песочницу для исторических Telegram-экспортов. Первый реальный датасет — многолетняя история «Клуб БРИЛЛИАНТОВЫЙ ГОЛОС», но shared Brain не получает Anna-specific веток.

Главный продуктовый вопрос:

> Какую повторяющуюся работу AI community co-host может забрать у администратора, сохранив её авторитет, культуру группы и право бота молчать?

## Current checkpoint — 2026-09-22

### Phase A/B — import / anonymize / frozen replay — DONE

Merged PR #130.

Реализовано:

- Telegram Desktop JSON importer;
- детерминированная анонимизация участников;
- remap message ids с сохранением reply topology;
- safe representation service/media/reactions;
- bounded episode builder;
- frozen replay cases с оригинальной human timeline;
- CLI `prepare`.

Privacy follow-ups:

- PR #133 — whole-name/token boundaries, URL/deep-link/source-id/title redaction, ambiguous owner fail-closed;
- PR #135 — payment/requisites blocks, включая adjacent recipient line; false-positive guards для обычных слов «перевод/карта».

Raw archive не коммитится и не импортируется в production DB/memory.

### Phase C — isolated behavioral replay — DONE

Merged PR #132.

Lab reuse текущих production Brain-компонентов:

- Scene Analyzer;
- Dispatcher;
- Personality Engine;
- Response Generator;
- ConnectorPreset (по умолчанию `education_community_v1`).

Выход на каждый frozen case:

- reply / ignore;
- reason codes;
- intervention score;
- generated response, только если Brain решил отвечать.

Честные parity limits:

- LONG memory / callback retrieval — disabled;
- statement watcher — disabled;
- dynamic cooldown / feedback history — not replayed;
- reminders/actions — disabled;
- Telegram outbox — disabled;
- simulated bot reply не меняет последующие historical turns.

### Phase C.5 — Group DNA + representative review pack — DONE

Merged PR #137.

Детерминированно выделяются только **операционные** паттерны:

- admin schedule announcements/reminders;
- schedule corrections/changes;
- access/join/link questions;
- material/recording/text questions;
- attendance/availability;
- newcomer joins;
- admin welcomes;
- gratitude/feedback.

Никакого психологического или sensitive profiling.

Outputs:

- `group_dna.json` — counts + sanitized evidence ids;
- `review_pack.json` — bounded sample, распределённый по архиву.

## CLI

### 1. Prepare

```bash
python -m app_v2.group_lab prepare \
  --input /private/result.json \
  --output-dir /private/group-lab \
  --owner-name "<admin display name>"
```

Outputs:

- `sanitized_history.json`;
- `frozen_replay.json`.

### 2. Analyze Group DNA

```bash
python -m app_v2.group_lab analyze \
  --history /private/group-lab/sanitized_history.json \
  --replay /private/group-lab/frozen_replay.json \
  --output-dir /private/group-lab \
  --sample-per-category 5
```

Outputs:

- `group_dna.json`;
- `review_pack.json`.

### 3. Run selected behavioral replay

```bash
python -m app_v2.group_lab run \
  --replay /private/group-lab/review_pack.json \
  --output /private/group-lab/behavior_review.json \
  --preset education_community_v1
```

## Privacy invariants

- source group id/title не попадают в lab-visible artifacts;
- raw Telegram `user...` / `channel...` ids не попадают в lab-visible artifacts;
- exact participant identities заменяются aliases;
- URLs/invite/deep links, handles, emails, phone-like values и long numeric identifiers редактируются;
- payment/requisites blocks редактируются;
- original filenames и reaction-user identities не сохраняются;
- real archive не используется в unit tests и не коммитится;
- Group Lab не пишет в production DB, memory, outbox или reminders.

## Что ещё НЕ сделано

- реальный многолетний архив ещё не прогнан целиком через текущий merged Group Lab;
- реальные `group_dna.json` / `review_pack.json` ещё не получены;
- representative cases ещё не размечены `join / stay_silent / assist_admin / uncertain`;
- current `education_community_v1` ещё не оценён на реальном review pack;
- clean Telegram sandbox для демонстрации администратору ещё не создаётся на этом этапе.

## Следующий gate

1. Запустить **реальный private export** через `prepare`.
2. Проверить privacy-safe output и статистику.
3. Построить `group_dna.json` + `review_pack.json`.
4. Сначала вручную проверить representative pack.
5. Только после этого запускать LLM behavioral replay на этой выборке.
6. По результатам уточнить профиль community co-host.
7. Затем — clean Telegram sandbox и acceptance перед приглашением администратора.

Не прогонять весь многолетний архив через LLM автоматически: сначала representative review pack.
