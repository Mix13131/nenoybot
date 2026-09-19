# ROADMAP — НеНой 2.0

## Актуальная контрольная точка — 2026-09-19

Рабочая линия — `v2`. Текущий проверенный code checkpoint: `aae85beccda6598fcdeb39225298f9232859e109` (merge PR #101).

После прежней точки PR #90 завершены:

- **TASK 29 / #92 — trustworthy memory + feedback + operation receipts**: PR #93 → `7d8b184...`, затем bounded replay/idempotency follow-up → `aa43b5d...`;
- **TASK 30 / #94 — calendar/timezone reminders**: PR #96 → `9462912...`, post-merge P2 → PR #98 `16739b9...`, финальный bounded parser follow-up → PR #101 `aae85be...`;
- последняя полная CI на финальном TASK 30 head: **469 passed, 1 warning** на isolated PostgreSQL 16 (run #253).

После финального bounded review два найденных P2 были закрыты в PR #101; после зелёного full CI новый широкий parser-аудит намеренно не запускался (D-049). Новый production deploy / live Telegram acceptance **ещё не подтверждён**. Runtime acceptance — следующий gate.

Полный Friends Test не объявлен завершённым. **Кнопку «🛑 Стоп» под напоминаниями не добавляем**: решение пользователя D-046 остаётся в силе.

Источник текущего состояния и границ проверки: [LIVE_TEST_STATUS.md](LIVE_TEST_STATUS.md). Принятые решения: [DECISIONS.md](DECISIONS.md), D-046–D-049.

---

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
25 Railway v2                ✅ functional live runtime (historical check)
26 Personal Smoke            ✅ functional PASS (2026-09-14)
27 Friends Preflight         🟡 group test occurred; full checklist not re-audited
28 Friends Test              🚧 live feedback / reminder fixes; not completed
```

После исходных 28 MVP-задач добавлен отдельный hardening-слой, не меняющий границы MVP:

```text
29 Trusted memory / feedback / truthful receipts   ✅ merged + bounded replay follow-up
30 Calendar/timezone reminders                     ✅ merged + bounded parser follow-up (#101)
Next gate: deploy + bounded live acceptance         ⏭️ not performed
```

Артефакт: `MVP_BUILD_PLAN.md`. Текущие live-оговорки — в `LIVE_TEST_STATUS.md`; отметки реализации и зелёный CI не заменяют production health-check/live acceptance.

---

## Phase 5 — Personal MVP 2.0

**Статус: LIVE — FIRST E2E PASS (историческая проверка)**

Зафиксированный production path:

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

- transport INFO logs могли записать credential-bearing Telegram Bot API URL — logging hardened в PR #73; на том этапе была необходима ротация старого токена;
- strict JSON schema Memory Mapper дала скрытый HTTP 400 — schema hardened в PR #74.

Позднее PR #84 описал восстановление webhook после смены токена. Завершённость ротации и актуальные настройки production при обновлении документации 2026-09-15 повторно не проверялись: не объявлять старую операционную задачу ни автоматически закрытой, ни необходимой к повторному выполнению без проверки.

Артефакты: `LIVE_TEST_STATUS.md`, `tasks/TASK_26_PERSONAL_SMOKE_TEST.md`.

---

## Phase 6 — Group MVP

**Статус: BUILT IN CODE — CONTROLLED LIVE HISTORY; LATEST DEPLOY PENDING**

Group-код уже умеет:

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

После начала группового тестирования закрыт цикл исправлений ручной отмены напоминаний:

- PR #88 — первая ручная отмена;
- PR #89 — отмена цепочки не превращается в mute самого НеНоя, есть ответ о результате;
- PR #90 — reply-stop конкретной цепочки, stop по адресату, stop всей текущей группы, подавление ожидающих событий/outbox, минимум 15 минут между повторениями и максимум 4 срабатывания по умолчанию.

Правило PR #90 разрешает участникам текущей группы останавливать цепочки независимо от автора; раннее ограничение PR #88 «только создатель» больше не является текущим правилом этих команд. Границы между группами сохраняются.

Код исправлений подтверждён в GitHub. Аварийная остановка одной старой цепочки и восстановление worker — исторический отчёт предыдущей сессии, не новая операция этой фиксации.

Артефакты: `tasks/TASK_27_FRIENDS_PREFLIGHT.md`, `LIVE_TEST_STATUS.md`.

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

**Статус: HARDENED IN TASK 29; LIVE ACCEPTANCE PENDING**

TASK 29 усилил Feedback/Memory слой: реакции и feedback привязываются к реальным bot interventions в том же scope, episode context bounded/replay-safe, evidence provenance валидируется, а memory/task/reminder confirmations опираются на фактические operation receipts.

Replay/idempotency gaps, найденные после основного merge, закрыты отдельным follow-up `aa43b5d...`.

Код и CI приняты; новый production deploy этой версии ещё не подтверждён.

---

## Phase 9 — Friends Test

**Статус: CODE HARDENING DONE; DEPLOY/LIVE ACCEPTANCE PENDING; ПОЛНЫЙ FRIENDS TEST НЕ ЗАКРЫТ**

Групповой тест уже дал реальные обращения и инциденты, из которых выросли TASK 29 и TASK 30. Кодовые исправления завершены до `v2@aae85be...`, но эта версия ещё не прошла отдельный production deploy/live acceptance. Это не означает, что все дни Friends Test пройдены или что текущий состав подключённых групп проверен заново.

План 7 дней живого теста остаётся ориентиром, а не отчётом о выполненных днях:

- День 1–2: low initiative, наблюдение.
- День 3–4: callbacks ON, medium initiative.
- День 5–7: высокий roast, profanity по настройке группы, adaptive initiative.

Для подключения новой группы сохраняется контролируемый preflight:

1. проверить безопасность текущих credentials; не повторять ротацию вслепую;
2. проверить настройку BotFather `/setprivacy` для передачи обычных групповых сообщений;
3. получить явный выбор конкретной тестовой группы;
4. добавить НеНоя 2.0 и получить хотя бы один group update;
5. активировать именно выбранную группу через whitelist с согласованным профилем;
6. провести direct mention + ordinary message smoke и проверить изоляцию её контекста.

Этот checklist не является указанием заново подключать уже работающую группу. Статус второй группы и полнота preflight в текущей документационной задаче не проверялись.

Следующий gate: отдельно согласованный deploy актуального `v2` и bounded live acceptance truthful receipts + calendar/timezone reminders + существующей отмены (reply / адресат / вся группа). Он не запускается автоматически этой документационной фиксацией и пока не отмечен как PASS.

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

Исключение, уже решённое пользователем: inline-кнопку «🛑 Стоп» под напоминаниями не добавлять и не переносить в эту фазу как отложенную задачу (D-046).

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

Отдельно от отложенных функций: кнопка «🛑 Стоп» под напоминаниями **отклонена**, а не запланирована после MVP.

# Critical Path

`Vision ✅ → Personality ✅ → Memory ✅ → Dispatcher ✅ → Architecture ✅ → Build 01–24 ✅ → Railway / Personal Smoke ✅ (исторические проверки) → TASK 29 trusted memory/receipts ✅ → TASK 30 calendar/timezone ✅ → Deploy + bounded live acceptance ⏭️ → Controlled Friends Test 🚧 → Product Review v0.2 → Closed Beta → Monetization`

Проверенная точка GitHub: `v2@aae85beccda6598fcdeb39225298f9232859e109` (PR #101). Следующее подтверждение — не новая функция, а deploy/live acceptance актуального кода по границам из `LIVE_TEST_STATUS.md`.
