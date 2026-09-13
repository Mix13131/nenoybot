# НеНой 2.0

Статус: **build plan complete / ready to implement**

НеНой 2.0 — отдельное развитие продукта. Версия 1 продолжает жить независимо и не должна ломаться изменениями v2.

## Правило веток

- `main` — текущая версия НеНой v1, стабильная линия.
- `v2` — отдельная линия разработки НеНой 2.0.
- Не сливать `v2` в `main` без отдельного решения.
- Новые продуктовые документы, спецификации и код v2 вести в ветке `v2`.

## Runtime separation

v2 будет иметь отдельный runtime-контур:

- отдельный Telegram Bot token;
- отдельный webhook;
- отдельные Railway web + worker services;
- отдельную PostgreSQL database;
- отдельные env-переменные;
- отдельный cost/analytics контур.

Новый runtime строится в `app_v2/`. Унаследованный `app/` из v1 временно остаётся reference implementation и не переписывается по частям в v2.

## Продуктовая формула

**НеНой помнит → замечает → действует → иногда подъёбывает.**

НеНой 2.0 работает в двух режимах:

1. **Personal** — тренер, зеркало, забота, ассистент, наблюдатель, действия.
2. **Group** — кореш, roast, callbacks, память компании, организация, арбитраж фактов, умение молчать.

## Ключевые документы

- [PRODUCT_VISION.md](./PRODUCT_VISION.md) — общее видение продукта.
- [ROADMAP.md](./ROADMAP.md) — продуктовый roadmap.
- [PERSONALITY_SPEC.md](./PERSONALITY_SPEC.md) — модель характера и поведения.
- [MEMORY_SPEC.md](./MEMORY_SPEC.md) — HOT/WARM/LONG, Memory Cards, retrieval, compaction и privacy.
- [DISPATCHER_SPEC.md](./DISPATCHER_SPEC.md) — ignore/reply/act/schedule, Intervention Score, cooldown, Silence Policy и model routing.
- [ARCHITECTURE.md](./ARCHITECTURE.md) — runtime, PostgreSQL queue, data model, outbox, workers, model routing, observability и deployment.
- [MVP_BUILD_PLAN.md](./MVP_BUILD_PLAN.md) — 28 последовательных задач реализации, build gates, acceptance criteria и обязательный формат отчёта Codex.
- [DECISIONS.md](./DECISIONS.md) — журнал принятых решений.

## Архитектурные принципы

1. Один Core, два режима: Personal и Group.
2. Personal Memory и Group Memory жёстко разделены по scope.
3. Сообщения превращаются в компактные Memory Cards вместо бесконечного контекста.
4. Умение молчать — функция системы.
5. Сильная модель вызывается только там, где действительно нужна.
6. Характер задаётся параметрами, а не одним огромным промптом.
7. У каждой группы свой roast/sarcasm/profanity/initiative profile.
8. LONG Memory растёт медленнее raw messages за счёт merge/decay/compaction.
9. Dispatcher объяснимо решает, когда говорить, действовать или молчать.
10. При деградации Group Mode становится тише, а не фантазирует.
11. Telegram webhook не ждёт LLM; тяжёлая работа идёт через durable PostgreSQL queue.
12. Для MVP нет Redis/Kafka/Celery/graph DB/vector DB без доказанной необходимости.
13. Outbox + idempotency защищают от duplicate replies.
14. Каждый AI-вызов логирует task kind, tokens, latency и cost.
15. Новый код v2 строится отдельно в `app_v2/`, не ломая legacy reference.
16. Одна задача Codex имеет ограниченный scope, acceptance criteria и реально запущенные tests.

## Текущий Critical Path

```text
Product Vision ✅
      ↓
Personality Spec ✅
      ↓
Memory Spec ✅
      ↓
Dispatcher Spec ✅
      ↓
Architecture ✅
      ↓
MVP Build Plan ✅
      ↓
TASK 01 — app_v2 Skeleton ← СЛЕДУЮЩИЙ ШАГ
      ↓
Personal MVP
      ↓
Group MVP
      ↓
Friends Test 😈
```

## Build gates

- **Gate A** — webhook + DB + event queue + worker + outbox без LLM.
- **Gate B** — рабочий Personal MVP с памятью и reminders.
- **Gate C** — Group MVP: silence, callbacks, roast, profanity и initiative.
- **Gate D** — отдельный Railway v2 runtime.
- **Gate E** — готовность к реальному Friends Test.

## Первый полигон

- Personal: использование владельцем бота.
- Group: один реальный чат друзей по whitelist.
- Главный сигнал Group успеха: другие участники сами начинают обращаться к НеНою без подталкивания владельца.
