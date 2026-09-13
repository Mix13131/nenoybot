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

Зафиксированы отдельный runtime v2, namespace `app_v2/`, Railway web + worker + PostgreSQL, Telegram webhook, PostgreSQL durable queue, Dispatcher, Scene Analyzer, Memory/Context/Personality layers, outbox, idempotency, model routing, cost tracking, privacy boundaries, observability, maintenance и deployment sequence.

Артефакт: `ARCHITECTURE.md`.

---

## Phase 4.5 — MVP Build Plan

**Статус: DONE**

Архитектура разбита на 28 маленьких последовательных задач Codex.

Для каждой стадии определены:

- scope;
- разрешённые изменения;
- запреты;
- acceptance criteria;
- обязательные tests;
- build gates;
- обязательная форма отчёта Codex.

Артефакт: `MVP_BUILD_PLAN.md`.

Build order:

```text
Stage A — Foundation
01 Skeleton
02 Contracts
03 DB
04 Webhook
05 Event Queue
06 Outbox

Stage B — Decision Brain
07 Dispatcher deterministic
08 Model Router
09 Scene Analyzer

Stage C — Memory + Character
10 Memory Repository
11 Memory Mapper
12 Personality Engine
13 Context Builder

Stage D — Personal MVP
14 Response Generator
15 Personal E2E
16 Actions / Reminders

Stage E — Group MVP
17 Group Whitelist
18 Group Silence First
19 Roast / Callback
20 Adaptive Initiative

Stage F — Feedback / Cost / Reliability
21 Feedback
22 Analytics / Cost
23 Maintenance
24 Reliability

Stage G — Deployment / Real Test
25 Railway v2
26 Personal Smoke
27 Friends Preflight
28 Friends Test
```

---

## Phase 5 — Personal MVP 2.0

**Статус: READY TO BUILD**

Реализация начинается с `TASK 01 — app_v2 Skeleton` и проходит через Gate A до Personal E2E.

Personal MVP должен уметь помнить пользователя, цели, проекты, решения и обещания; использовать callbacks; выбирать Coach/Mirror/Care/Assistant/Observer; создавать задачи/напоминания и проявлять контролируемую инициативу.

Тест: 7–14 дней реального использования владельцем после deployment.

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

`Vision ✅ → Personality ✅ → Memory ✅ → Dispatcher ✅ → Architecture ✅ → MVP Build Plan ✅ → TASK 01 Skeleton ← NEXT → Gate A → Personal MVP → Group MVP → Friends Test → Analytics/Economics → v0.2 → Closed Beta → Monetization`
