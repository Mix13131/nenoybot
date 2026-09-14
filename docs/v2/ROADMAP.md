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

Архитектура разбита на 28 маленьких последовательных задач.

Build order:

```text
Stage A — Foundation
01 Skeleton                  ✅
02 Contracts                 ✅
03 DB                        ✅
04 Webhook                   ✅
05 Event Queue               ✅
06 Outbox                    ✅

Stage B — Decision Brain
07 Dispatcher deterministic  ✅
08 Model Router              ✅
09 Scene Analyzer            ✅

Stage C — Memory + Character
10 Memory Repository         ✅
11 Memory Mapper             ✅
12 Personality Engine        ✅
13 Context Builder           ✅

Stage D — Personal MVP
14 Response Generator        ✅
15 Personal E2E              ✅
16 Actions / Reminders       ✅

Stage E — Group MVP
17 Group Whitelist           ✅
18 Group Silence First       ✅
19 Roast / Callback          ✅
20 Adaptive Initiative       ✅

Stage F — Feedback / Cost / Reliability
21 Feedback                  ✅
22 Analytics / Cost          ✅
23 Maintenance               ✅
24 Reliability               ✅

Stage G — Deployment / Real Test
25 Railway v2                ✅ functional live runtime
26 Personal Smoke            ✅ functional PASS
27 Friends Preflight         🚧 code ready, live setup pending
28 Friends Test              ⏳ next after preflight
```

Артефакт: `MVP_BUILD_PLAN.md`.

---

## Phase 5 — Personal MVP 2.0

**Статус: LIVE — FIRST E2E PASS**

Рабочий production path уже существует:

```text
Telegram
→ webhook
→ PostgreSQL event
→ worker
→ Scene Analyzer
→ Dispatcher
→ Memory / Context / Personality
→ OpenAI generator
→ Outbox
→ Telegram Sender
```

14 сентября 2026 прошёл первый настоящий Personal smoke: Telegram webhook вернул 200, worker обработал событие, OpenAI generation прошёл, Telegram sendMessage вернул 200, пользователь получил первый ответ НеНоя 2.0.

Первый live-pass сразу обнаружил два production-нюанса:

- transport INFO logs могли записать credential-bearing Telegram Bot API URL — logging hardened в PR #73; старый token требуется ротировать;
- strict JSON schema Memory Mapper дала скрытый HTTP 400 — schema hardened в PR #74.

Артефакты: `LIVE_TEST_STATUS.md`, `tasks/TASK_26_PERSONAL_SMOKE_TEST.md`.

---

## Phase 6 — Group MVP

**Статус: BUILT — CONTROLLED PREFLIGHT**

Group runtime уже умеет:

- explicit whitelist;
- participant context;
- silence-first;
- direct mention/reply;
- Group Memory isolation;
- roast/callback gates;
- running jokes;
- adaptive initiative;
- feedback;
- temporary silence;
- analytics.

Для Friends Test добавлен DB-backed admin CLI:

```text
python -m app_v2.group_admin list
python -m app_v2.group_admin activate-friends <chat_id>
python -m app_v2.group_admin deactivate <chat_id>
```

Неизвестные группы не активируются автоматически.

Артефакт: `tasks/TASK_27_FRIENDS_PREFLIGHT.md`.

---

## Phase 7 — Roast Engine

**Статус: MVP BUILT**

Логика MVP:

`opportunity → target/context → grounded memory → callback/running joke → sensitivity gates → profanity ceiling → timing → reply/silence`

Принцип:

> Не придумывать шутку из воздуха. Замечать смешное в контексте и добивать.

Live tuning будет происходить только по результатам Friends Test.

---

## Phase 8 — Feedback Loop

**Статус: MVP BUILT**

Собираются реакции, replies, organic re-mentions, explicit negative feedback и mute/silence signals. Feedback связывается с intervention и используется Adaptive Initiative.

---

## Phase 9 — Friends Test

**Статус: PREFLIGHT IN PROGRESS**

7 дней живого теста:

- День 1–2: low initiative, наблюдение.
- День 3–4: callbacks ON, medium initiative.
- День 5–7: высокий roast, profanity по настройке группы, adaptive initiative.

Перед стартом TASK 28 остаются только live-операции:

1. ротировать Telegram token, который попал в private runtime log до logging fix;
2. BotFather → `/setprivacy` → v2 bot → **Disable**, иначе Telegram не будет передавать обычную групповую болтовню;
3. выбрать один конкретный чат друзей;
4. добавить туда НеНой 2.0 и дать Telegram прислать хотя бы один group update;
5. активировать именно этот `chat_id` через whitelist с Day-1 profile;
6. провести direct mention + ordinary message smoke.

Главная Group North Star:

> **Количество участников, кроме владельца, которые сами начали обращаться к НеНою.**

Дополнительно: mentions, replies, reactions, roast hit rate, memory requests, reminders, ignored interventions, negative feedback, mute requests, bot removed.

---

## Phase 10 — Product Review v0.2

**Статус: AFTER FRIENDS TEST**

Не добавлять функции автоматически. Сначала разобрать данные живого теста: roast hit/miss, callbacks, ошибки памяти, навязчивость, organically used functions. После этого корректировать thresholds, personality, mapper, context builder и initiative.

---

## Phase 11 — Economics & Cost Tracking

**Статус: BASELINE BUILT**

Для AI-вызовов уже пишутся model, tokens, latency, success и estimated cost; есть Personal/Group analytics reporting.

Внутренние ориентиры:

- Personal normal: AI COGS < $3 / month;
- Group normal: AI COGS < $3 / month;
- Heavy usage: контролируемый верхний диапазон.

Реальную экономику считать после Friends Test на живом usage.

---

## Phase 12 — Settings UX

**Статус: PLANNED AFTER PRODUCT REVIEW**

Personal: жёсткость, юмор, подъёб, мат, инициативность, забота, память, напоминания.

Group: roast, sarcasm, profanity level/frequency, initiative, callbacks, max interventions, sensitivity.

---

## Phase 13 — Closed Beta

**Статус: PLANNED**

5–10 групп разных типов: друзья, семья, неформальная команда, клуб/сообщество. Проверить Character Engine в разных социальных средах.

---

## Phase 14 — Monetization

**Статус: PLANNED**

После подтверждения retention и реальной себестоимости: Free / Personal / Personal Pro / Group / Personal + Group bundle.

---

## Phase 15 — Platform Expansion

**Статус: PLANNED AFTER CORE VALIDATION**

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

`Vision ✅ → Personality ✅ → Memory ✅ → Dispatcher ✅ → Architecture ✅ → Build 01–24 ✅ → Railway ✅ → Personal Smoke ✅ → Friends Preflight 🚧 → Friends Test → Product Review v0.2 → Closed Beta → Monetization`
