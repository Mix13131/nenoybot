# ROADMAP — НеНой 2.0

## Phase 0 — Product Vision

**Статус: DONE**

Зафиксирована продуктовая модель: один Core, Personal и Group, Memory Map, Personality Engine, Dispatcher, Context Builder, Action Engine и Analytics.

Артефакт: `PRODUCT_VISION.md`.

---

## Phase 1 — Personality Specification

**Статус: DONE**

Зафиксированы Core Personality, Personal/Group profiles, roast, sarcasm, profanity, initiative, callback, care, sensitivity, Participant Adaptation, Situational Override и feedback adaptation.

Артефакт: `PERSONALITY_SPEC.md`.

---

## Phase 2 — Memory Specification

**Статус: DONE**

Зафиксированы Personal/Group isolation, HOT/WARM/LONG memory, Memory Cards, evidence, quality fields, relations, merge/decay/archive, running jokes, callback fatigue, retrieval, compaction, retention и acceptance tests.

Артефакт: `MEMORY_SPEC.md`.

---

## Phase 3 — Dispatcher & Intervention Logic

**Статус: DONE**

Зафиксированы Event Envelope, Social Energy, `ignore/reply/act/schedule`, Intervention Score, hard gates, Silence Policy, Group cooldown/limits, Personal/Group mode selection, Roast/Callback Gates, reason codes, model tiers, degradation policy и feedback loop.

Артефакт: `DISPATCHER_SPEC.md`.

---

## Phase 4 — Technical Architecture

**Статус: DONE**

Зафиксированы:

- отдельный runtime v2;
- новый namespace `app_v2/` вместо постепенного переписывания legacy `app/`;
- отдельные Railway web + worker + PostgreSQL;
- быстрый Telegram webhook;
- Event Ingestor;
- PostgreSQL durable queue без Redis;
- Dispatcher / Scene Analyzer;
- Context Builder;
- Personality Engine;
- Memory Mapper / Retrieval Engine;
- Response Generator;
- Action Engine;
- Scheduler;
- Feedback Collector;
- Outbox;
- idempotency;
- schema основных таблиц;
- task-based model routing;
- cost tracking;
- privacy boundaries;
- observability;
- error/degradation policy;
- maintenance jobs;
- testing strategy;
- deployment sequence;
- architecture acceptance criteria.

Артефакт: `ARCHITECTURE.md`.

---

## Phase 4.5 — MVP Build Plan

**Статус: NEXT**

Превратить архитектуру в последовательность маленьких задач для Codex.

Каждая задача должна иметь:

- цель;
- ограниченный scope;
- какие файлы разрешено менять;
- что нельзя трогать;
- acceptance criteria;
- обязательные tests/checks;
- rollback / failure notes;
- форму отчёта после выполнения.

Предварительный critical build order:

```text
01 Skeleton app_v2
02 Configuration + contracts
03 DB migrations
04 Telegram webhook ingest
05 PostgreSQL event queue
06 Worker + idempotency
07 Dispatcher deterministic policy
08 Scene Analyzer + Model Router
09 Memory repositories / mapper / retrieval
10 Context Builder + Personality Engine
11 Response Generator
12 Outbox + Telegram sender
13 Actions / Reminders / Scheduler
14 Feedback Collector
15 Analytics + cost tracking
16 Personal E2E
17 Group E2E
18 Railway v2 deployment
19 Personal smoke test
20 Friends Group test
```

Артефакт: `MVP_BUILD_PLAN.md`.

---

## Phase 5 — Personal MVP 2.0

**Статус: PLANNED**

MVP должен уметь помнить пользователя, цели, проекты, решения и обещания; использовать callbacks; выбирать Coach/Mirror/Care/Assistant/Observer; создавать задачи/напоминания и проявлять контролируемую инициативу.

Тест: 7–14 дней реального использования владельцем.

Проверяем false memory, полезность callbacks, неуместные вмешательства, ощущение «он меня знает», раздражение и переключение режимов.

---

## Phase 6 — Group MVP

**Статус: PLANNED**

Один реальный чат друзей через whitelist.

Первая версия должна различать участников, читать HOT context, создавать Group Memory Cards, отвечать при обращении, иногда вмешиваться сама, использовать callbacks/running jokes, делать roast, учитывать profanity profile и уметь молчать.

---

## Phase 7 — Roast Engine

**Статус: PLANNED**

Логика:

`opportunity → target → context → callback → running joke → profanity → timing → reply/silence`

Принцип:

> Не придумывать шутку из воздуха. Замечать смешное в контексте и добивать.

---

## Phase 8 — Feedback Loop

**Статус: PLANNED**

Собирать позитивные/негативные/нейтральные сигналы после вмешательств и связывать их с intervention/reason codes для последующей адаптации.

---

## Phase 9 — Friends Test

**Статус: PLANNED**

7 дней живого теста.

- День 1–2: low initiative, наблюдение.
- День 3–4: callbacks ON, medium initiative.
- День 5–7: высокий roast, profanity по настройке группы, adaptive initiative.

Главная Group North Star:

> **Количество участников, кроме владельца, которые сами начали обращаться к НеНою.**

Дополнительно: mentions, replies, reactions, roast hit rate, memory requests, reminders, ignored interventions, negative feedback, mute requests, bot removed.

---

## Phase 10 — Product Review v0.2

Не добавлять функции автоматически. Сначала разобрать данные живого теста: roast hit/miss, callbacks, ошибки памяти, навязчивость, organically used functions. После этого корректировать thresholds, personality, mapper, context builder и initiative.

---

## Phase 11 — Economics & Cost Tracking

Для каждого AI-вызова считать model, tokens, latency, cost и task kind.

Внутренние ориентиры:

- Personal normal: AI COGS < $3 / month;
- Group normal: AI COGS < $3 / month;
- Heavy usage: контролируемый верхний диапазон.

Экономику считать на реальном usage.

---

## Phase 12 — Settings UX

Personal: жёсткость, юмор, подъёб, мат, инициативность, забота, память, напоминания.

Group: roast, sarcasm, profanity level/frequency, initiative, callbacks, max interventions, sensitivity.

---

## Phase 13 — Closed Beta

5–10 групп разных типов: друзья, семья, неформальная команда, клуб/сообщество. Проверить Character Engine в разных социальных средах.

---

## Phase 14 — Monetization

После подтверждения retention и реальной себестоимости: Free / Personal / Personal Pro / Group / Personal + Group bundle.

---

## Phase 15 — Platform Expansion

Только после подтверждения core-продукта: Voice, Telegram Mini App, Web UI, Calendar, Gmail, Drive, Web Search, agents, shared tasks, другие мессенджеры.

---

# Не строим до подтверждения MVP

- отдельное mobile app;
- Redis без доказанной необходимости;
- Kafka/Celery;
- graph DB;
- dedicated vector DB;
- Kubernetes;
- десятки агентов;
- сложную тарификацию;
- огромную админку;
- integrations-first архитектуру.

# Critical Path

`Vision ✅ → Personality ✅ → Memory ✅ → Dispatcher ✅ → Architecture ✅ → MVP Build Plan ← NEXT → Personal MVP → Group MVP → Friends Test → Analytics/Economics → v0.2 → Closed Beta → Monetization`
