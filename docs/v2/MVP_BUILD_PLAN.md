# MVP_BUILD_PLAN v1.0 — НеНой 2.0

## 1. Назначение

Этот документ превращает продуктовые и архитектурные спецификации НеНой 2.0 в последовательность маленьких, проверяемых задач для Codex.

Цель — не «сразу написать нового бота», а собирать работающий end-to-end runtime небольшими вертикальными шагами, после каждого из которых репозиторий остаётся в понятном и проверяемом состоянии.

Главный принцип:

> **Одна задача Codex = один ограниченный кусок системы + конкретные acceptance criteria + реально запущенные проверки.**

---

## 2. Неприкосновенные правила разработки

### 2.1 Изоляция v1

По умолчанию Codex НЕ имеет права менять:

- `app/`
- `tests/`
- существующие prompts/assets v1
- runtime v1
- deployment v1

Новый runtime строится в:

```text
app_v2/
tests_v2/
docs/v2/
```

Изменение legacy-файла допускается только если конкретная задача явно это разрешает.

### 2.2 Ветка

Base branch для реализации: `v2`.

Рекомендуемый рабочий процесс:

```text
v2
 ↓
короткая task branch
 ↓
реализация + tests
 ↓
review
 ↓
merge обратно в v2
```

Не сливать v2 в `main`.

### 2.3 Никакой лишней инфраструктуры

До отдельного решения запрещено добавлять:

- Redis;
- Celery;
- RabbitMQ;
- Kafka;
- graph database;
- dedicated vector database;
- Kubernetes;
- отдельный frontend;
- тяжёлый admin panel.

PostgreSQL остаётся DB + durable queue.

### 2.4 Никаких скрытых «улучшений»

Codex не должен одновременно с задачей:

- массово рефакторить соседний код;
- менять архитектуру;
- переименовывать публичные contracts;
- добавлять библиотеки «на будущее»;
- менять product rules;
- менять personality/memory/dispatcher policy без отдельного решения.

Если видит проблему вне scope — фиксирует её в отчёте, но не чинит самовольно.

### 2.5 Tests — только реально запущенные

Нельзя писать «тесты проходят», если команда фактически не запускалась.

После каждой задачи должны быть указаны:

- точная команда;
- результат;
- число passed/failed/skipped, если применимо.

---

## 3. Definition of Done для каждой задачи

Задача считается выполненной только если одновременно:

1. scope задачи закрыт;
2. acceptance criteria подтверждены;
3. tests/checks реально запущены;
4. нет неожиданных изменений legacy v1;
5. ошибки не скрыты;
6. docs/config обновлены, если задача этого требует;
7. Codex дал полный отчёт по форме из конца документа.

---

# BUILD STAGE A — FOUNDATION

Цель Stage A: получить отдельный runtime v2, который запускается, подключается к PostgreSQL, принимает Telegram-shaped events и надёжно проводит их через очередь без LLM.

---

## TASK 01 — `app_v2` Skeleton

### Цель

Создать минимальный отдельный Python runtime v2, который импортируется и запускается независимо от `app/`.

### Разрешено менять

- `app_v2/**`
- `tests_v2/**`
- `requirements.txt`
- `.env.example` только для добавления v2-переменных без удаления v1

### Создать минимум

```text
app_v2/
├── __init__.py
├── main.py
├── config.py
├── domain/
├── adapters/
├── repositories/
├── services/
├── workers/
├── prompts/
└── db/

tests_v2/
├── unit/
├── integration/
├── contracts/
└── scenarios/
```

### Требования

- FastAPI web entry point;
- `/health` возвращает `200`;
- `/ready` существует;
- импорт `app_v2` не зависит от legacy `app`;
- config загружается из env;
- отсутствующий обязательный production secret должен давать понятную ошибку конфигурации, а не stacktrace без контекста.

### Acceptance

- `pytest tests_v2` запускается;
- health endpoint покрыт тестом;
- `python -c "import app_v2"` успешен;
- `app/` не изменён.

---

## TASK 02 — Domain Contracts + Enums

### Цель

Зафиксировать typed contracts, на которых строятся следующие сервисы.

### Разрешено менять

- `app_v2/domain/**`
- `tests_v2/contracts/**`

### Реализовать

Минимум:

- `EventEnvelope`
- `SceneAnalysis`
- `DispatcherDecision`
- `MemoryCard`
- `MemoryRelation`
- `PersonalityState`
- `ActionRequest`
- `OutboundMessage`
- `FeedbackEvent`
- `LLMUsageRecord`
- enums для scope/action/mode/status/reason codes.

### Требования

Contracts должны соответствовать:

- `PERSONALITY_SPEC.md`
- `MEMORY_SPEC.md`
- `DISPATCHER_SPEC.md`
- `ARCHITECTURE.md`

Telegram-specific objects не должны попадать в domain contracts.

### Acceptance

- valid fixtures сериализуются/десериализуются;
- invalid scope/action/status отклоняются;
- Group Memory contract требует group scope;
- tests contracts проходят.

---

## TASK 03 — PostgreSQL Schema + Migration Runner

### Цель

Создать схему отдельной v2 database без ORM-магии и без зависимости от v1 schema.

### Разрешено менять

- `app_v2/db/**`
- `app_v2/adapters/postgres.py`
- `tests_v2/integration/**`
- `requirements.txt`, если действительно требуется

### Таблицы MVP

```text
users
chats
chat_members
messages
events
memory_cards
memory_relations
tasks
reminders
interventions
feedback_events
outbox
llm_usage
```

### Решение MVP

Использовать versioned SQL migrations. Не добавлять Alembic только ради одной первой схемы, если собственный простой migration runner достаточен.

### Обязательные constraints

- unique Telegram identities;
- unique inbound `telegram_update_id`;
- unique `(chat_id, telegram_message_id)`;
- unique outbox `dedupe_key`;
- scope indexes для memory;
- indexes для pending events/reminders/outbox;
- timestamps в UTC.

### Acceptance

- чистая DB мигрируется с нуля;
- повторный запуск migrations безопасен;
- integration test подтверждает основные constraints;
- schema v1 не используется.

---

## TASK 04 — Telegram Update Normalizer + Webhook

### Цель

Принимать Telegram update, быстро нормализовать его и записывать в v2 DB без AI-вызовов.

### Разрешено менять

- `app_v2/main.py`
- `app_v2/adapters/telegram_webhook.py`
- `app_v2/services/event_ingestor.py`
- нужные repositories
- `tests_v2/integration/**`

### Поддержать минимум

- private text message;
- group text message;
- reply-to-bot;
- mention metadata;
- edited message;
- reaction event, если доступен в Telegram update format;
- unknown update должен безопасно игнорироваться/логироваться.

### Требования

- verify webhook secret;
- dedupe по update id;
- upsert user/chat/member;
- store raw message;
- create normalized pending event;
- вернуть HTTP 200 без LLM.

### Acceptance

- duplicate Telegram update не создаёт второй event;
- webhook path не вызывает OpenAI adapter;
- private и group fixtures создают правильный `EventEnvelope`;
- integration tests проходят.

---

## TASK 05 — PostgreSQL Event Queue + Worker Claim

### Цель

Сделать durable processing loop без Redis.

### Разрешено менять

- `app_v2/repositories/event_repo.py`
- `app_v2/workers/event_worker.py`
- `app_v2/workers/main.py`
- `tests_v2/integration/**`

### Реализовать

Lifecycle:

```text
pending → processing → completed
                     ↘ retry
                     ↘ failed
```

Claim через `FOR UPDATE SKIP LOCKED`.

### Требования

- retry counter;
- exponential/backoff policy с configurable upper bound;
- stale processing recovery;
- один event не должен одновременно обрабатываться двумя workers;
- обработчик пока может быть stub/no-op.

### Acceptance

- concurrent claim test;
- retry test;
- stale-event recovery test;
- event completion test.

---

## TASK 06 — Outbox + Telegram Sender

### Цель

Сразу закрыть надёжную outbound delivery, чтобы дальше любой pipeline не отправлял Telegram напрямую.

### Разрешено менять

- `app_v2/domain/**` при необходимости только без breaking changes
- `app_v2/repositories/outbox_repo.py`
- `app_v2/adapters/telegram_sender.py`
- `app_v2/workers/outbox_worker.py`
- `tests_v2/**`

### Требования

- generator/services только создают `OutboundMessage` / outbox record;
- отдельный worker отправляет Telegram message;
- retries;
- `dedupe_key`;
- Telegram API error сохраняется;
- `reply_to_message_id` поддерживается.

### Acceptance

- повторная постановка одного `dedupe_key` не даёт duplicate send;
- mocked Telegram send проходит;
- retry/failure path покрыт тестом.

### Gate A

После TASK 06 система должна уметь:

```text
fake Telegram update
→ webhook
→ DB event
→ worker claim
→ test handler
→ outbox
→ mocked Telegram sender
```

Без LLM. Это первый end-to-end infrastructure gate.

---

# BUILD STAGE B — DECISION BRAIN

Цель Stage B: НеНой умеет понять сцену и принять объяснимое решение, но ещё не обязан иметь богатую память и характер.

---

## TASK 07 — Deterministic Dispatcher Policy

### Цель

Реализовать всё, что по `DISPATCHER_SPEC` не требует LLM.

### Разрешено менять

- `app_v2/services/dispatcher.py`
- domain decision/reason enums
- intervention repository
- `tests_v2/unit/**`

### Реализовать минимум

- direct mention/reply hard intents;
- mute/silent_until;
- cooldown;
- soft/hard daily unsolicited limits;
- duplicate protection assumptions;
- score thresholds;
- hard blockers;
- Personal default reply policy;
- Group default silence policy;
- stable reason codes;
- policy version.

### Acceptance

Scenario tests минимум:

- direct mention replies despite cooldown;
- ordinary group message → ignore;
- group muted → ignore;
- serious context blocks roast;
- hard daily limit blocks unsolicited;
- Personal message returns reply mode candidate.

---

## TASK 08 — OpenAI Adapter + Model Router + Usage Capture

### Цель

Сделать единственную точку AI-вызовов и cost-aware model routing.

### Разрешено менять

- `app_v2/adapters/openai_adapter.py`
- `app_v2/services/model_router.py`
- `app_v2/services/cost_tracker.py`
- usage repository
- config/env
- tests

### Логические роли

```text
MODEL_CLASSIFIER
MODEL_MEMORY
MODEL_GENERATOR
MODEL_DEEP
```

### Требования

- concrete model names только в config;
- timeout;
- retry только там, где безопасно;
- structured JSON response support;
- input/output/cached tokens;
- latency;
- success/failure;
- estimated cost record;
- test fake adapter без реального API.

### Acceptance

- business services не содержат hardcoded model names;
- failed call создаёт usage record;
- unit tests model routing проходят.

---

## TASK 09 — Scene Analyzer

### Цель

Получать компактную `SceneAnalysis`, необходимую Dispatcher.

### Разрешено менять

- `app_v2/services/scene_analyzer.py`
- `app_v2/prompts/scene_analyzer.md`
- tests

### Выход минимум

- banter;
- seriousness;
- conflict;
- sensitivity;
- roast opportunity;
- callback opportunity hint;
- help opportunity;
- memory value;
- contradiction signal;
- commitment/decision signals.

### Требования

- structured output;
- bounds `0..1`;
- timeout/failure → conservative safe defaults;
- direct deterministic signals не надо просить модель угадывать заново.

### Acceptance

- fixture «обычная болтовня»;
- fixture «реальный конфликт»;
- fixture «явная отмазка/contradiction»;
- invalid model JSON безопасно деградирует.

---

# BUILD STAGE C — MEMORY + CHARACTER

---

## TASK 10 — Memory Repository + Scope Boundary

### Цель

Реализовать безопасное CRUD/retrieval основание LONG Memory.

### Разрешено менять

- `app_v2/repositories/memory_repo.py`
- `app_v2/services/retrieval_engine.py`
- tests

### Критическое правило

Каждый query памяти требует explicit:

```text
scope_type + scope_id
```

Group retrieval никогда не fallback-ится в Personal.

### Реализовать

- create/update/archive;
- evidence;
- usage policy filters;
- confidence/freshness/status filters;
- basic relational relevance retrieval;
- callback fatigue filter.

### Acceptance

Обязательный privacy test:

> факт существует в Personal scope пользователя → тот же пользователь пишет в Group scope → Group retrieval не может вернуть Personal card.

Этот тест должен оставаться regression test навсегда.

---

## TASK 11 — Memory Mapper

### Цель

Превращать raw events в Memory Candidate/Update без бесконечного роста карточек.

### Разрешено менять

- `app_v2/services/memory_mapper.py`
- `app_v2/prompts/memory_mapper.md`
- memory repo
- tests

### Поддержать MVP types

- goal/project;
- commitment;
- decision;
- plan/event;
- preference;
- observation;
- contradiction;
- quote;
- running_joke candidate;
- pattern candidate.

### Требования

- explicit «запомни» имеет высокий приоритет;
- explicit «забудь» выполняется без model guess;
- model inference не становится confirmed fact без evidence;
- duplicate semantic cards обновляются/merge, а не плодятся;
- pattern promotion требует thresholds из Memory Spec.

### Acceptance

- repeated commitment updates one logical memory;
- pattern не создаётся после одного эпизода;
- forget command;
- stale/contradicted memory path;
- mapper failure не ломает direct reply pipeline.

---

## TASK 12 — Personality Engine

### Цель

Считать effective personality state по профилю, режиму, участнику и сцене.

### Разрешено менять

- `app_v2/services/personality_engine.py`
- tests

### Pipeline

```text
Core
→ Personal/Group profile
→ Participant adaptation
→ Mode modifiers
→ Situational override
→ clamp 0..10
→ Final Personality State
```

### Обязательно

- `profanity_level` и `profanity_frequency` — разные параметры;
- group maximum нельзя превысить temporary request;
- serious context снижает roast;
- Personal Care снижает pressure/challenge;
- deterministic engine, без LLM.

### Acceptance

Table-driven unit tests всех конфликтов приоритетов.

---

## TASK 13 — Context Builder

### Цель

Собрать компактный generation package вместо передачи всей истории.

### Разрешено менять

- `app_v2/services/context_builder.py`
- retrieval/message repositories
- tests

### Контекст

- текущая scene;
- ограниченный HOT context;
- 3–8 relevant Memory Cards;
- effective Personality State;
- Dispatcher mode/reasons;
- target participant;
- task/action state при необходимости.

### Guardrails

- LONG memory budget ориентир 800–1200 tokens;
- Personal/Group scope обязателен;
- archived/forbidden memories не попадают;
- source/evidence сохраняется для factual callback.

### Acceptance

- большой raw chat не создаёт огромный generation context;
- privacy regression test;
- token-budget test.

---

# BUILD STAGE D — RESPONSE + PERSONAL MVP

---

## TASK 14 — Response Generator

### Цель

Генерировать финальную реплику только после Dispatcher decision.

### Разрешено менять

- `app_v2/services/response_generator.py`
- `app_v2/prompts/personal_generator.md`
- `app_v2/prompts/group_generator.md`
- tests

### Требования

- separate Personal/Group generator prompts;
- Personality State передаётся компактно;
- режимы поддерживаются;
- не придумывать callback без предоставленной Memory Card;
- Group roast не должен объяснять собственную шутку;
- generation failure не создаёт duplicate retries/send.

### Acceptance

Golden/scenario tests структуры prompt package; LLM output не фиксировать хрупкими exact-string tests.

---

## TASK 15 — Personal End-to-End v0

### Цель

Первый реальный вертикальный продуктовый срез.

### Flow

```text
private Telegram message
→ ingest
→ Scene Analysis
→ Personal Dispatcher
→ Personal memory retrieval
→ Personality
→ Context Builder
→ Generator
→ intervention log
→ outbox
→ Telegram
```

### Поддержать

- ordinary conversation;
- assistant;
- coach;
- mirror;
- care;
- explicit remember/forget;
- relevant callback.

### Acceptance

Scenario suite минимум 10 Personal scenarios.

### Gate B

После Task 15 можно вручную разговаривать с v2 Personal runtime через test Telegram bot/local Telegram sandbox после deployment setup.

---

## TASK 16 — Action Engine + Reminders

### Цель

Добавить реальные действия, необходимые Personal MVP.

### Разрешено менять

- `app_v2/services/action_engine.py`
- task/reminder repositories
- `app_v2/workers/scheduler.py`
- tests

### MVP actions

- create task;
- update/complete task;
- create reminder;
- cancel reminder;
- reschedule reminder;
- reminder due → event → normal Dispatcher pipeline.

### Требования

- timezone aware;
- recurring reminders только если надёжно поддерживаются;
- idempotent firing;
- reminder outbound имеет deterministic dedupe key.

### Acceptance

- one-time reminder;
- cancelled reminder;
- restart-safe reminder;
- duplicate scheduler tick не дублирует message.

---

# BUILD STAGE E — GROUP MVP

---

## TASK 17 — Group Whitelist + Membership Context

### Цель

Разрешить Group Mode только явно разрешённым тестовым группам.

### Реализовать

- whitelist check;
- participant identity/upsert;
- group profile load;
- participant profile load;
- unapproved group → no active participation.

### Acceptance

- approved group processed;
- unknown group не получает ответов;
- private functionality не ломается.

---

## TASK 18 — Group End-to-End Silence First

### Цель

Запустить Group Mode, где главная способность — не мешать.

### Flow

```text
group message
→ ingest
→ Scene Analyzer
→ Retrieval
→ Dispatcher
→ ignore OR direct reply
```

На этом этапе unsolicited initiative можно держать feature flag OFF.

### Acceptance

- ordinary chat → silence;
- direct mention → reply;
- reply-to-bot → reply;
- serious conflict → no unsolicited roast;
- Group callback не видит Personal memory.

---

## TASK 19 — Roast + Callback + Running Joke Engine

### Цель

Включить главное живое поведение Friends Group.

### Реализовать

- Roast Gate;
- Callback Gate;
- running joke retrieval;
- callback fatigue;
- group profanity level/frequency;
- target participant adaptation;
- context-based roast mode;
- feature flag для unsolicited intervention.

### Принцип

> Не генерировать случайную остроту. Roast должен иметь конкретный reason code и опираться на сцену/callback/pattern.

### Acceptance

Scenario suite:

- «уже еду» callback;
- переобувание;
- broken promise;
- exhausted running joke → silence/other mode;
- profanity=0;
- profanity=high but serious scene → suppression;
- roast target with negative adaptation → softer/silence.

---

## TASK 20 — Group Cooldown + Adaptive Initiative

### Цель

Включить controlled unsolicited interventions.

### Стартовая policy

- min cooldown: 12 min;
- soft daily limit: 6;
- hard daily limit: 10;
- configurable per group;
- bot share guardrail;
- negative feedback increases silence quickly.

### Acceptance

- direct mention работает даже после hard unsolicited limit;
- two ignored unsolicited interventions увеличивают cooldown;
- «заткнись» ставит `silent_until`;
- positive reactions не повышают initiative резко.

### Gate C

После Task 20 Group MVP функционально готов к закрытому тесту, но ещё не выпускается до observability/deployment tasks.

---

# BUILD STAGE F — FEEDBACK, COST, RELIABILITY

---

## TASK 21 — Feedback Collector

### Цель

Связать реакцию людей с конкретным intervention.

### Поддержать

- emoji reaction;
- reply to bot;
- organic re-mention;
- explicit negative feedback;
- mute/shut-up intent.

### Требования

Сохранять raw feedback отдельно от derived adaptation.

### Acceptance

- reaction привязывается к intervention;
- негативный feedback изменяет effective initiative policy;
- одна reaction update не дублируется.

---

## TASK 22 — Analytics + Cost Dashboard Queries

### Цель

Не строить UI, но иметь SQL/CLI отчёт, показывающий здоровье продукта и COGS.

### Минимальные метрики

Personal:

- messages;
- replies;
- proactive messages;
- memory writes;
- callbacks;
- model cost.

Group:

- participant count;
- organic participants;
- direct mentions;
- unsolicited interventions;
- reaction rate;
- roast hit rate;
- ignored interventions;
- negative feedback;
- mute events;
- AI cost/group/day.

### Acceptance

CLI/report query на fixture DB выдаёт метрики без внешнего BI.

---

## TASK 23 — Maintenance + Retention + Compaction Jobs

### Цель

Не дать DB и памяти бесконечно расти.

### Реализовать

- WARM message retention;
- expired raw text cleanup;
- memory freshness/decay maintenance;
- duplicate candidate compaction;
- running joke fatigue maintenance;
- stale event/outbox cleanup/recovery.

### Acceptance

- обычный raw text старше retention очищается;
- evidence, нужное LONG Memory, не теряется;
- active memory soft cap запускает compaction, а не blind delete.

---

## TASK 24 — Reliability / Failure Scenarios

### Цель

Проверить, что система деградирует безопасно.

### Обязательные сценарии

- OpenAI unavailable;
- Telegram send 5xx/timeout;
- DB worker restart;
- event retry;
- outbox retry;
- malformed classifier JSON;
- memory retrieval failure;
- duplicate webhook;
- duplicate scheduler tick.

### Expected behavior

- Group unsolicited при неопределённости чаще молчит;
- direct Personal может ответить без LONG-memory claims;
- duplicate user-visible messages не появляются.

---

# BUILD STAGE G — DEPLOYMENT + REAL TEST

---

## TASK 25 — Railway v2 Deployment

### Цель

Поднять полностью отдельный runtime НеНой 2.0.

### Ресурсы

```text
nenoy-v2-web
nenoy-v2-worker
nenoy-v2-postgres
```

### Требования

- deploy только ветки `v2`/approved build;
- отдельные env;
- отдельный Telegram token;
- отдельный webhook secret;
- health/readiness;
- migrations до traffic;
- v1 deployment не изменяется.

### Acceptance

- `/health` green;
- `/ready` подтверждает DB;
- worker работает;
- test Telegram update проходит полный path;
- v1 продолжает работать независимо.

---

## TASK 26 — Personal Smoke Test

### Цель

Использовать НеНой 2.0 лично 1–3 дня перед допуском в группу.

### Проверить вручную

- естественность;
- modes;
- remember/forget;
- callbacks;
- false memory;
- reminders;
- cost;
- error logs;
- duplicate sends.

### Exit criteria

Нет критических privacy/idempotency ошибок и нет систематической ложной памяти.

---

## TASK 27 — Friends Group Preflight

### Цель

Подготовить один реальный group chat.

### Настройки стартового профиля

```text
roast             9
sarcasm           9
humor             9
callback         10
profanity_level   8
profanity_frequency 5
initiative        LOW initially
```

Конкретный profanity profile владелец может изменить перед запуском.

### Проверить

- whitelist;
- bot privacy mode / Telegram permissions;
- reactions ingestion;
- group member mapping;
- personal/group isolation;
- emergency mute command;
- feature flags.

---

## TASK 28 — Friends Test Release

### Цель

7 дней естественного теста, описанного в Roadmap.

### День 1–2

- unsolicited initiative low/off;
- наблюдение;
- memory map building.

### День 3–4

- callbacks on;
- medium initiative.

### День 5–7

- adaptive initiative;
- full roast profile;
- profanity по group settings.

### North Star

> **Сколько участников, кроме владельца, сами начали обращаться к НеНою.**

### После теста

Не добавлять функции сразу. Сначала Product Review v0.2 по intervention logs, feedback, cost и memory quality.

---

# 4. Build Gates

## Gate A — Infrastructure

После Task 06:

- webhook;
- DB;
- event queue;
- worker;
- outbox;
- sender.

Никакого AI ещё не требуется.

## Gate B — Personal MVP

После Task 16:

- Personal conversation;
- memory;
- personality;
- dispatcher;
- actions/reminders;
- full outbound path.

## Gate C — Group MVP

После Task 20:

- group silence;
- direct replies;
- callbacks;
- roast;
- profanity profile;
- initiative/cooldown.

## Gate D — Production-like test readiness

После Task 25:

- isolated Railway runtime;
- isolated DB;
- v2 Telegram bot;
- logs/cost/retries/idempotency.

## Gate E — Friends Test

После Task 27 preflight.

---

# 5. Порядок задач — нельзя перепрыгивать без причины

```text
01 Skeleton
02 Contracts
03 DB
04 Webhook
05 Event Queue
06 Outbox
      ↓ Gate A
07 Dispatcher deterministic
08 Model Router
09 Scene Analyzer
10 Memory Repository
11 Memory Mapper
12 Personality Engine
13 Context Builder
14 Response Generator
15 Personal E2E
16 Actions/Reminders
      ↓ Gate B
17 Group Whitelist
18 Group Silence First
19 Roast/Callback
20 Adaptive Initiative
      ↓ Gate C
21 Feedback
22 Analytics/Cost
23 Maintenance
24 Reliability
25 Railway Deployment
      ↓ Gate D
26 Personal Smoke
27 Friends Preflight
28 Friends Test
      ↓ Gate E
Product Review v0.2
```

---

# 6. Стандарт постановки задачи Codex

Каждая отдельная задача должна передаваться Codex примерно в таком формате:

```text
TASK XX — <название>

ЦЕЛЬ
<одна конкретная цель>

КОНТЕКСТ
- branch: v2
- authoritative docs: <список>
- existing completed dependencies: <список>

РАЗРЕШЕНО МЕНЯТЬ
- ...

ЗАПРЕЩЕНО МЕНЯТЬ
- app/**
- runtime v1
- product policies outside task scope
- любые лишние файлы без объяснения

СДЕЛАТЬ
1. ...
2. ...
3. ...

ACCEPTANCE CRITERIA
- ...

ОБЯЗАТЕЛЬНО ПРОВЕРИТЬ
- точные команды tests/checks

НЕ ДЕЛАТЬ
- не расширять scope
- не скрывать ошибки
- не заявлять о тестах без их запуска

ПОСЛЕ ВЫПОЛНЕНИЯ
Вернуть обязательный отчёт по форме проекта.
```

---

# 7. Обязательная форма отчёта Codex

После КАЖДОЙ задачи Codex обязан вернуть:

```text
1. ЧТО СДЕЛАНО
- конкретные изменения
- созданные/изменённые файлы

2. КАК ПРОВЕРЕНО
- точные команды, которые реально запускались
- фактический результат каждой команды
- passed / failed / skipped, если применимо

3. ЧТО ПРОВЕРИТЬ ВРУЧНУЮ
- только то, что реально нельзя подтвердить автоматическими tests

4. ЧТО НЕ СДЕЛАНО
- всё, что осталось вне scope
- незакрытые проблемы/ограничения

5. ОШИБКИ И РИСКИ
- все обнаруженные ошибки
- flaky/temporary решения
- предположения

6. ИЗМЕНЕНИЯ ВНЕ ЗАДАЧИ
- перечислить любые изменённые файлы вне разрешённого scope и объяснить зачем
- если таких нет: «Нет»

7. СЛЕДУЮЩИЙ РЕКОМЕНДУЕМЫЙ ШАГ
- ровно один логичный следующий шаг
```

Запрещён отчёт вида «всё готово, тесты пройдены» без доказательств.

---

# 8. Общий MVP Exit Criteria

НеНой 2.0 допускается к Friends Test только если:

- v1 не изменён и работает отдельно;
- v2 runtime полностью изолирован;
- inbound/outbound idempotency проверена;
- Personal/Group memory isolation имеет автоматический regression test;
- direct mention и silence policy работают;
- group profanity profile работает;
- callback опирается только на разрешённую evidence-backed memory;
- reminder не дублируется после restart;
- LLM usage/cost пишется;
- negative feedback может быстро приглушить инициативу;
- есть emergency group mute;
- нет известных критических privacy/data-loss багов;
- тестовый чат можно отключить одним config/DB flag без redeploy.

---

# 9. Что сознательно НЕ входит в этот MVP

- Telegram Mini App;
- web dashboard;
- voice;
- Gmail/Calendar/Drive;
- web search agent;
- межгрупповая shared memory;
- перенос Personal memory в Group;
- отдельная vector DB;
- graph DB;
- fine-tuning;
- автоматическая оплата;
- публичный self-service onboarding групп;
- сложная multi-tenant admin system.

Сначала доказываем главное:

> **Личный НеНой полезен благодаря памяти и инициативе, а Group НеНой ощущается живым участником, которого люди сами начинают звать в разговор.**
