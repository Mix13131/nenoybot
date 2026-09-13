# ARCHITECTURE v1.0 — НеНой 2.0

## 1. Цель архитектуры

НеНой 2.0 должен существовать отдельно от v1, но развиваться в том же репозитории и переиспользовать только те части старой версии, которые действительно подходят.

Архитектура MVP должна решать пять задач:

1. быстро принимать Telegram events;
2. надёжно и дёшево принимать решение `ignore / reply / act / schedule`;
3. хранить компактную карту памяти, а не бесконечный контекст;
4. безопасно разделять Personal и Group scopes;
5. собирать аналитику и реальную себестоимость каждого AI-вызова.

Главный принцип:

> **Минимум инфраструктуры, максимум контролируемости поведения.**

---

## 2. Изоляция v1 и v2

### Ветки

- `main` — текущая v1;
- `v2` — отдельная линия разработки НеНой 2.0.

### Runtime isolation

Для v2 создаётся отдельный runtime-контур:

- отдельный Telegram bot token;
- отдельный webhook;
- отдельный Railway web service;
- отдельный Railway worker service;
- отдельная PostgreSQL database;
- отдельные env variables;
- отдельные логи и cost accounting.

v1 и v2 не используют общую operational database.

### Код

Ветка `v2` исторически содержит унаследованный `app/` от v1. Не надо превращать его по частям в v2.

Новый runtime строится в отдельном namespace:

```text
app_v2/
```

Старый `app/` временно остаётся как reference implementation. Когда v2 полностью проходит end-to-end тесты, старый runtime внутри ветки v2 можно удалить отдельным решением.

Это снижает риск случайно смешать старую и новую архитектуру.

---

## 3. Общая схема

```text
Telegram
   │
   ▼
Webhook Adapter
   │
   ▼
Event Ingestor
   │
   ├── persist raw message/event
   ├── idempotency check
   └── enqueue normalized event
   │
   ▼
PostgreSQL Event Queue
   │
   ▼
Worker
   │
   ▼
Scene Analyzer
   │
   ▼
Dispatcher
   │
   ├── IGNORE
   ├── MEMORY
   ├── ACTION
   ├── SCHEDULE
   └── REPLY
          │
          ▼
   Context Builder
          │
          ├── HOT context
          ├── relevant Memory Cards
          ├── Personality State
          └── action / mode context
          │
          ▼
   Response Generator
          │
          ▼
   Outbox
          │
          ▼
Telegram Sender

Parallel services:
- Memory Mapper
- Retrieval Engine
- Personality Engine
- Action Engine
- Scheduler
- Feedback Collector
- Cost Tracker
- Maintenance Jobs
```

---

## 4. Почему не обрабатываем webhook целиком синхронно

Telegram webhook должен быстро получить `200 OK`.

Не надо держать HTTP request открытым, пока:

- вызывается LLM;
- строится память;
- ищутся callbacks;
- генерируется ответ;
- Telegram API отвечает повторно.

Webhook делает только:

1. проверяет secret;
2. нормализует update;
3. deduplicate по `update_id`;
4. пишет событие в БД;
5. возвращает `200`.

Целевой ориентир webhook latency:

> обычно < 300–500 ms без внешних AI-вызовов.

Вся тяжёлая работа выполняется worker.

---

## 5. Queue без Redis

На MVP не нужны Redis, RabbitMQ, Kafka или Celery.

PostgreSQL используется как durable event/job queue.

Worker получает события через паттерн:

```sql
SELECT ...
FROM events
WHERE status = 'pending'
  AND available_at <= now()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

После claim:

```text
pending → processing → completed
                     ↘ retry
                     ↘ failed
```

Плюсы:

- одна инфраструктура;
- durability;
- retry;
- idempotency;
- можно добавить второй worker без смены архитектуры.

Redis добавляется только если реальные данные покажут, что PostgreSQL queue становится bottleneck.

---

## 6. Railway runtime

Минимум три отдельных ресурса v2.

### `nenoy-v2-web`

Ответственность:

- Telegram webhook;
- health endpoints;
- readiness;
- будущие admin endpoints.

Команда ориентировочно:

```text
uvicorn app_v2.main:app --host 0.0.0.0 --port $PORT
```

### `nenoy-v2-worker`

Ответственность:

- consume events;
- Scene Analyzer;
- Dispatcher;
- Memory Mapper;
- generation;
- outbox delivery;
- scheduler loop;
- maintenance tasks.

Команда:

```text
python -m app_v2.workers.main
```

### `nenoy-v2-postgres`

Полностью отдельная БД v2.

На MVP Redis не используется.

---

## 7. Предлагаемая структура проекта

```text
app_v2/
├── main.py
├── config.py
│
├── domain/
│   ├── events.py
│   ├── decisions.py
│   ├── memory.py
│   ├── personality.py
│   ├── actions.py
│   └── enums.py
│
├── adapters/
│   ├── telegram_webhook.py
│   ├── telegram_sender.py
│   ├── openai_adapter.py
│   └── postgres.py
│
├── repositories/
│   ├── event_repo.py
│   ├── message_repo.py
│   ├── memory_repo.py
│   ├── intervention_repo.py
│   ├── reminder_repo.py
│   ├── feedback_repo.py
│   └── usage_repo.py
│
├── services/
│   ├── event_ingestor.py
│   ├── scene_analyzer.py
│   ├── dispatcher.py
│   ├── context_builder.py
│   ├── personality_engine.py
│   ├── retrieval_engine.py
│   ├── memory_mapper.py
│   ├── response_generator.py
│   ├── action_engine.py
│   ├── feedback_collector.py
│   └── cost_tracker.py
│
├── workers/
│   ├── main.py
│   ├── event_worker.py
│   ├── scheduler.py
│   ├── outbox_worker.py
│   └── maintenance.py
│
├── prompts/
│   ├── scene_analyzer.md
│   ├── memory_mapper.md
│   ├── personal_generator.md
│   └── group_generator.md
│
└── db/
    ├── migrations/
    └── schema.sql

tests_v2/
├── unit/
├── integration/
├── contracts/
└── scenarios/
```

Главное правило: domain layer не знает о Telegram, Railway или конкретной модели OpenAI.

---

## 8. Domain contracts

Ключевые контракты должны быть обычными typed Python objects / dataclasses / Pydantic models.

Минимальный набор:

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

Это позволяет тестировать мозг отдельно от Telegram.

---

## 9. Минимальная схема БД

### `users`

Хранит Telegram identity и Personal settings.

Ключевые поля:

- `id`
- `telegram_user_id UNIQUE`
- `display_name`
- `timezone`
- `personal_profile JSONB`
- `created_at`
- `updated_at`

### `chats`

- `id`
- `telegram_chat_id UNIQUE`
- `chat_type` (`private`, `group`)
- `title`
- `group_profile JSONB`
- `is_whitelisted`
- `is_active`
- `silent_until`

### `chat_members`

- `chat_id`
- `user_id`
- `role`
- `participant_profile JSONB`
- `joined_at`
- `last_seen_at`

Unique: `(chat_id, user_id)`.

### `messages`

WARM/raw store.

- `id`
- `chat_id`
- `user_id`
- `telegram_message_id`
- `reply_to_message_id`
- `text`
- `message_type`
- `created_at`
- `expires_at`

Unique: `(chat_id, telegram_message_id)`.

Обычный raw text очищается по retention policy, ориентир MVP — 30 дней.

### `events`

Durable inbox / queue.

- `id`
- `event_id UNIQUE`
- `telegram_update_id UNIQUE NULLABLE`
- `event_type`
- `scope_type`
- `scope_id`
- `actor_user_id`
- `payload JSONB`
- `status`
- `attempt_count`
- `available_at`
- `last_error`
- `created_at`
- `processed_at`

### `memory_cards`

Согласно `MEMORY_SPEC.md`.

Ключевые поля:

- `id`
- `scope_type`
- `scope_id`
- `type`
- `subject_user_id NULLABLE`
- `summary`
- `payload JSONB`
- `importance`
- `confidence`
- `freshness`
- `status`
- `origin`
- `usage_policy JSONB`
- `evidence JSONB`
- `created_at`
- `last_confirmed_at`
- `last_used_at`

Обязательный index по `(scope_type, scope_id, status)`.

### `memory_relations`

- `id`
- `scope_type`
- `scope_id`
- `from_memory_id`
- `relation_type`
- `to_memory_id`
- `weight`
- `created_at`

### `tasks`

- `id`
- `scope_type`
- `scope_id`
- `owner_user_id`
- `title`
- `status`
- `due_at`
- `payload JSONB`

### `reminders`

- `id`
- `scope_type`
- `scope_id`
- `target_user_id`
- `due_at`
- `recurrence_rule NULLABLE`
- `status`
- `payload JSONB`
- `last_fired_at`

### `interventions`

Audit trail решений НеНоя.

- `id`
- `event_id`
- `scope_type`
- `scope_id`
- `primary_action`
- `mode`
- `intervention_score`
- `reason_codes JSONB`
- `policy_version`
- `selected_memory_ids JSONB`
- `generated_text NULLABLE`
- `created_at`

### `feedback_events`

- `id`
- `intervention_id NULLABLE`
- `scope_id`
- `user_id`
- `feedback_type`
- `value`
- `payload JSONB`
- `created_at`

### `outbox`

Надёжная отправка наружу.

- `id`
- `dedupe_key UNIQUE`
- `channel`
- `destination_id`
- `payload JSONB`
- `status`
- `attempt_count`
- `available_at`
- `last_error`
- `sent_at`

### `llm_usage`

- `id`
- `event_id NULLABLE`
- `intervention_id NULLABLE`
- `task_kind`
- `model`
- `input_tokens`
- `output_tokens`
- `cached_tokens NULLABLE`
- `estimated_cost_usd`
- `latency_ms`
- `success`
- `created_at`

Это позволяет считать реальный COGS по пользователю, группе, feature и model tier.

---

## 10. Почему settings пока JSONB

На MVP не нужна отдельная таблица для каждого personality parameter.

`personal_profile`, `group_profile`, `participant_profile` хранят versioned JSONB.

Пример:

```json
{
  "version": 1,
  "roast": 9,
  "sarcasm": 9,
  "profanity_level": 8,
  "profanity_frequency": 5,
  "initiative": 6,
  "callback": 10
}
```

Когда настройки стабилизируются и появятся реальные запросы к отдельным полям, часть можно вынести в columns.

---

## 11. Idempotency

Telegram может прислать retry. Worker может упасть после частичного выполнения.

Поэтому idempotency обязательна.

### Inbound

`telegram_update_id` уникален.

Повторный webhook:

```text
update already exists
→ return 200
→ не создаём новое событие
```

### Message

Unique `(chat_id, telegram_message_id)`.

### Outbound

Каждая отправка имеет `dedupe_key`.

Например:

```text
reply:event_123
reminder:rem_456:2026-09-14T09:00
```

Повторный worker не должен отправить одно сообщение дважды.

---

## 12. End-to-end flow — Group message

```text
1. Telegram sends update
2. Webhook verifies secret
3. Event Ingestor normalizes event
4. user/chat/member upsert
5. raw message stored
6. event inserted pending
7. HTTP 200
8. Worker claims event
9. deterministic checks
10. Scene Analyzer evaluates scene
11. Retrieval Engine gets small relevant memory set
12. Dispatcher returns Decision Contract
13. decision = ignore?
      yes → intervention logged → complete
      no  → continue
14. secondary memory/action operations routed
15. Context Builder prepares generation package
16. Personality Engine computes effective state
17. Response Generator calls selected model
18. intervention recorded
19. outbound message inserted into outbox
20. Telegram Sender sends message
21. outbox marked sent
22. later reactions/replies create Feedback Events
23. adaptation / analytics update
```

---

## 13. End-to-end flow — Personal message

В Personal Mode direct conversation обычно требует reply.

```text
message
→ ingest
→ scene analysis
→ retrieve relevant personal memory
→ Dispatcher chooses mode
→ possible memory/action request
→ Context Builder
→ Personality Engine
→ Generator
→ Outbox
→ Telegram
```

Разница с Group: нет default silence policy для обычного прямого сообщения пользователя.

---

## 14. Memory pipeline

Memory не должна блокировать каждый ответ.

### Fast path

Для текущей реплики:

- HOT context;
- existing relevant Memory Cards;
- explicit memory commands.

### Async-ish mapping inside worker

После или параллельно generation можно запустить:

- candidate extraction;
- dedup;
- update existing card;
- merge;
- relation creation.

Если Memory Mapper временно недоступен, ответ пользователю не должен падать целиком.

Memory failure логируется и может быть retried отдельно.

---

## 15. Retrieval Engine

MVP retrieval не требует vector DB.

Порядок:

1. scope filter — обязательно;
2. status / usage policy filter;
3. participant / type / recency filters;
4. lexical / structured matching;
5. relevance scoring;
6. top 3–8 cards;
7. token budget check.

Позже `pgvector` можно добавить внутрь Retrieval Engine без изменения остальной системы.

Context Builder не знает, каким способом найден memory result.

---

## 16. Model Router

Модели не должны быть захардкожены по всему проекту.

Используется task-based routing.

```text
MODEL_CLASSIFIER
MODEL_MEMORY
MODEL_GENERATOR
MODEL_DEEP
```

### Tier 0 — rules only

LLM не вызывается:

- duplicate;
- whitelist rejection;
- mute;
- cooldown;
- hard limits;
- простые команды;
- deterministic routing.

### Tier 1 — lightweight

Используется для:

- Scene Analysis;
- memory candidates;
- intent classification;
- summarization/compaction.

### Tier 2 — standard generator

Используется для большинства Personal/Group replies.

### Tier 3 — deep/strong

Только для действительно сложных Personal reasoning cases или задач, где качество оправдывает стоимость.

Главное правило:

> Дорогая модель не решает, нужно ли вызывать дорогую модель.

---

## 17. Context Builder budget

Каждый generation request собирается из ограниченных частей:

```text
1. Core Personality contract
2. Final Personality State
3. Current mode
4. HOT conversation slice
5. 3–8 relevant Memory Cards
6. current task/action context
7. response contract
```

Не отправляем:

- всю историю пользователя;
- весь чат;
- все Memory Cards;
- гигантский общий system prompt.

Цель — компактный и предсказуемый контекст.

---

## 18. Action Engine

MVP actions:

- create task;
- update task;
- create reminder;
- reschedule reminder;
- cancel reminder;
- explicit memory write/forget;
- temporary silence.

Action Engine принимает только структурированный `ActionRequest`.

LLM не пишет SQL и не выполняет arbitrary commands.

---

## 19. Scheduler

Scheduler работает в worker service.

Каждые несколько секунд / десятков секунд:

```text
find due reminders
→ claim rows
→ create normalized reminder_due events
→ Dispatcher handles them like any other event
```

Это важно: reminder не имеет отдельного «особого бота». Он снова проходит через тот же personality / context / decision pipeline.

Для recurring reminders сохраняется следующая дата после успешного fire.

---

## 20. Feedback Collector

Feedback — это тоже событие.

Источники:

- reaction added;
- reply to bot;
- organic mention;
- explicit praise;
- explicit negative feedback;
- mute request;
- ignored unsolicited message.

Feedback Collector связывает сигнал с `intervention_id`, если это возможно.

После этого:

- пишется `feedback_events`;
- обновляются analytics;
- participant/group adaptation может меняться медленно по правилам Personality Spec.

---

## 21. Group whitelist

До Closed Beta Group Mode работает только для явно разрешённых чатов.

`chats.is_whitelisted = true`.

Если бот добавлен в неизвестную группу:

- не обрабатываем разговор через AI;
- можно отправить одно служебное сообщение или молчать согласно onboarding policy;
- raw conversation не складируется как обычный Group dataset.

Первый разрешённый scope — тестовый чат друзей.

---

## 22. Privacy boundary

Memory scope — обязательная часть каждого repository query.

Запрещён API вида:

```python
get_memories(user_id)
```

если не указан scope.

Нужен контракт примерно такого уровня:

```python
get_memories(
    scope_type="group",
    scope_id=group_id,
    ...
)
```

или Personal scope.

Group Context Builder никогда не делает fallback в Personal memory.

Cross-scope transfer в MVP отсутствует.

---

## 23. Security

Минимум для MVP:

- Telegram webhook secret token;
- bot token только в env;
- OpenAI key только в env;
- DB credentials только в env;
- никакие secrets не логируются;
- message text не пишется в обычные error logs без необходимости;
- whitelist для групп;
- explicit delete/forget operations;
- raw retention job;
- backups управляются отдельно через Railway/Postgres policy.

---

## 24. Observability

Каждый event получает `trace_id`.

Structured logs минимум:

```text
trace_id
event_id
event_type
scope_type
scope_id
primary_action
mode
reason_codes
model
latency_ms
success/error
```

Не логируем полный message text по умолчанию.

### Health endpoints

`GET /health`

- process alive.

`GET /ready`

- database available;
- required config loaded.

### MVP dashboards можно строить SQL-запросами

Не нужны Prometheus/Grafana на старте.

Ключевые operational metrics:

- events pending;
- events failed;
- webhook errors;
- worker latency;
- generation latency;
- outbox retries;
- LLM error rate;
- cost/day;
- cost/user;
- cost/group.

---

## 25. Error handling

### Telegram send error

- retry through outbox;
- exponential-ish backoff;
- hard failure after configurable attempts.

### Lightweight classifier unavailable

Group unsolicited:

> prefer silence.

Direct Personal / direct mention:

> fallback to safe standard reply path without fabricated memory callbacks.

### Memory retrieval unavailable

- отвечать без LONG callbacks;
- не выдумывать память;
- логировать degraded mode.

### Generator unavailable

- retry limited number of times;
- не отправлять технический мусор пользователю;
- mark event failed / degraded.

Главный принцип:

> При деградации НеНой становится тише, а не увереннее.

---

## 26. Maintenance jobs

Worker периодически выполняет:

- delete expired raw messages;
- memory freshness decay;
- compact / merge candidates;
- archive low-value stale cards;
- cleanup old completed events;
- retry failed retryable jobs;
- calculate daily usage/cost summaries.

Не надо создавать отдельный сервис под каждый maintenance job.

---

## 27. Versioning

В данных сохраняются версии:

- `dispatcher_policy_version`;
- `personality_profile_version`;
- `memory_mapper_version`;
- `prompt_version`;
- `model`.

Иначе после Friends Test невозможно будет понять, почему вчера реплика работала, а сегодня нет.

---

## 28. Testing strategy

### Unit

- score calculation;
- hard gates;
- cooldown;
- profanity clamp;
- memory scope filters;
- retrieval scoring;
- recurrence calculations.

### Contract

- LLM structured outputs validate schema;
- Dispatcher Decision Contract;
- Memory Mapper Contract;
- ActionRequest Contract.

### Integration

- webhook → event row;
- duplicate webhook;
- event → ignore;
- event → reply → outbox;
- reminder → event → send;
- memory write → retrieval;
- feedback → adaptation signal.

### Scenario tests

Отдельные живые сценарии:

- Personal Coach;
- Personal Care;
- Personal Mirror;
- Group silence;
- Group callback;
- Group roast;
- Group serious conflict;
- «НеНой, заткнись»;
- false-memory prevention;
- Personal memory isolation from Group.

---

## 29. Что специально не добавляем в MVP

- Redis;
- Kafka;
- Celery;
- Neo4j;
- dedicated vector database;
- microservices per engine;
- Kubernetes;
- отдельный analytics stack;
- отдельный admin frontend;
- agent framework;
- multi-messenger abstraction beyond необходимого adapter boundary.

Если реальная нагрузка потребует — добавим. До этого это архитектурный шум.

---

## 30. Рекомендуемые зависимости

Текущий v1 минимален. Для v2 предполагаются:

```text
fastapi
uvicorn
pydantic
psycopg[binary]
openai
python-dotenv
pytest
pytest-asyncio
```

Дополнительные библиотеки добавляются только при реальной необходимости.

ORM не является обязательным. На MVP можно использовать `psycopg` + явные repositories + SQL migrations, чтобы схема и запросы оставались прозрачными.

---

## 31. Deployment sequence

Порядок первого развёртывания:

1. создать отдельного Telegram bot v2;
2. создать Railway services `nenoy-v2-web`, `nenoy-v2-worker`, Postgres;
3. добавить v2 env;
4. применить migrations;
5. deploy branch `v2`;
6. проверить `/health` и `/ready`;
7. поставить Telegram webhook с secret;
8. whitelist только владельца / тестовую группу;
9. включить Personal Mode;
10. после smoke tests включить Friends Group;
11. наблюдать events/interventions/cost/error metrics.

v1 продолжает работать независимо всё это время.

---

## 32. Architecture acceptance criteria

Архитектура считается реализованной на уровне MVP, если:

1. v1 и v2 могут работать одновременно;
2. webhook v2 отвечает быстро и не зависит от LLM latency;
3. duplicate Telegram update не приводит к duplicate reply;
4. worker можно перезапустить без потери pending events;
5. Personal и Group memory scopes не пересекаются;
6. Group unknown chat не получает полный AI runtime;
7. `ignore` event не вызывает генератор;
8. каждый LLM-call имеет usage/cost record;
9. reminder переживает restart worker;
10. outbox не отправляет один и тот же reply дважды;
11. raw message retention очищается автоматически;
12. degraded memory/classifier не приводит к fabricated callback;
13. end-to-end scenario `Telegram → decision → reply → feedback` проходит тестом.

---

## 33. Следующий документ

После этой архитектуры нужен `MVP_BUILD_PLAN.md`.

Он должен превратить архитектуру в последовательность маленьких задач для Codex с жёсткими acceptance criteria и отчётом после каждой задачи.

Critical path реализации:

```text
Skeleton
→ DB migrations
→ Telegram ingest
→ Postgres queue
→ Worker
→ Dispatcher rules
→ Model Router
→ Memory
→ Context Builder
→ Personality
→ Generator
→ Outbox
→ Actions/Reminders
→ Feedback
→ Analytics/Cost
→ Personal test
→ Group test
```
