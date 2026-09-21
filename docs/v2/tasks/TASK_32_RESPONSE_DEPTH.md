# TASK 32 — Response Depth / Progressive Disclosure

## Почему

Live-тест 2026-09-21 показал, что НеНой может превращать обычный экспертный вопрос в многоэкранную консультацию. Отдельный regression-case: фраза `Поясни детальнее, не понятно` расширила тему вместо того, чтобы сделать предыдущий тезис понятнее.

Нормативное решение: [DECISIONS.md](../DECISIONS.md), D-052; [PERSONALITY_SPEC.md](../PERSONALITY_SPEC.md), §2.1.

## Scope

Только runtime/prompt policy v2:

- определить intent глубины ответа по текущему сообщению;
- передать его Generator в generation package;
- научить Personal и Group Generator различать `concise / clarify / detail / deep`;
- сохранить существующий `personality.brevity` как параметр стиля, не смешивая его с охватом ответа;
- не вводить глобальный жёсткий лимит символов/токенов;
- не менять Dispatcher, Memory, scheduler, reminders, database schema, `main` или legacy `app/`.

## Контракт

### `concise`
Главный вывод + 1–2 существенных пояснения + при необходимости один ближайший шаг.

### `clarify`
Сделать предыдущий тезис проще и яснее, не расширяя тему. Ключевой regression-case:

`Поясни детальнее, не понятно` → `clarify`, а не `detail/deep`.

### `detail`
Добавить один следующий слой деталей к текущему тезису.

### `deep`
Разрешить полноценный разбор только при явном сильном запросе: `разбери подробно`, `исследуй`, `все нюансы`, `пошагово`, `дай инструкцию` и эквиваленты.

## Safety boundary

Критичные safety / medical / legal / compliance оговорки нельзя выкидывать ради краткости. При этом основной вывод должен идти первым.

## Acceptance criteria

- [x] Generator получает `response_depth` в payload.
- [x] Обычный вопрос классифицируется как `concise`.
- [x] `Поясни детальнее, не понятно` классифицируется как `clarify`.
- [x] `Расскажи подробнее` классифицируется как `detail`.
- [x] Явный глубокий запрос классифицируется как `deep`.
- [x] Personal prompt знает контракт всех четырёх режимов.
- [x] Group prompt знает контракт всех четырёх режимов.
- [x] Удалено старое допущение, что `объясни нормально` само по себе разрешает многоэкранный разбор.
- [x] Unit regression tests зелёные.
- [x] Full `tests_v2` зелёный до merge: **570 passed, 1 warning**.
- [ ] После deploy повторяется живой Telegram regression-case.

## Реализация

PR #126 merged в `v2`, merge commit `9697171b1bcf927bce465dedec052859a209f6fc`. Railway web/worker после deploy — `SUCCESS`; live Telegram regression re-test остаётся открытым.

## Что не считать PASS

Prompt/code merge сам по себе не доказывает продуктовый результат. Финальный live PASS ставится только после реального Telegram ответа на тот же сценарий.
