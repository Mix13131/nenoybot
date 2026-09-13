# ROADMAP — НеНой 2.0

## Phase 0 — Product Vision

**Статус: DONE**

Зафиксировать продуктовую модель:

- один Core;
- Personal и Group как два режима;
- Memory Map;
- Personality Engine;
- Dispatcher;
- Context Builder;
- Action Engine;
- Analytics.

Артефакт: `PRODUCT_VISION.md`.

---

## Phase 1 — Personality Specification

**Статус: IN PROGRESS**

Определить все поведенческие параметры НеНоя:

- directness;
- brevity;
- warmth;
- pressure;
- humor;
- sarcasm;
- roast;
- profanity level;
- profanity frequency;
- initiative;
- callback;
- challenge;
- care;
- playfulness;
- sensitivity.

Также описать:

- Core Personality;
- Personal Profile;
- Group Profile;
- Participant Adaptation;
- Situational Override;
- порядок приоритетов настроек;
- медленную адаптацию по feedback signals.

Артефакт: `PERSONALITY_SPEC.md`.

---

## Phase 2 — Memory Specification

**Следующий этап**

Определить:

- HOT / WARM / LONG memory;
- структуру `Memory Card`;
- типы памяти;
- importance / confidence / freshness;
- source references;
- создание, обновление, merge, decay и archive;
- memory relations;
- правила privacy scope;
- алгоритм сжатия большого потока сообщений в компактную карту.

Артефакт: `MEMORY_SPEC.md`.

---

## Phase 3 — Dispatcher & Intervention Logic

Определить pipeline:

`event → understand → remember? → act? → speak? → mode`

Нужны:

- ignore / remember / reply / act / schedule;
- Intervention Score;
- cooldown;
- limits per day;
- serious-context override;
- direct mentions;
- callback opportunities;
- roast opportunities;
- реакция на игнор и негативные сигналы.

Артефакт: `DISPATCHER_SPEC.md`.

---

## Phase 4 — Technical Architecture

Спроектировать MVP без лишней инфраструктуры.

Базовые компоненты:

- Telegram webhook;
- Event Ingestor;
- Personal Engine;
- Group Engine;
- Dispatcher;
- Context Builder;
- Personality Engine;
- Memory Mapper;
- Scheduler;
- Feedback Collector;
- Analytics;
- PostgreSQL.

Минимальные сущности БД:

- users;
- chats;
- chat_members;
- messages;
- memory_cards;
- memory_relations;
- tasks;
- reminders;
- interventions;
- feedback_events;
- settings.

Артефакт: `ARCHITECTURE.md`.

---

## Phase 5 — Personal MVP 2.0

Обновить личного НеНоя на новой архитектуре.

MVP должен уметь:

- помнить пользователя;
- помнить цели и проекты;
- хранить обещания и решения;
- видеть повторяющиеся паттерны;
- использовать callbacks;
- выбирать между Coach / Mirror / Care / Assistant / Observer;
- создавать задачи;
- создавать напоминания;
- проявлять контролируемую инициативу.

Тест: 7–14 дней реального использования владельцем.

Основные проверки:

- ложные воспоминания;
- полезные callbacks;
- неуместные вмешательства;
- ощущение «он меня знает»;
- раздражение;
- качество переключения режимов.

---

## Phase 6 — Group MVP

Подключить только один реальный чат друзей через whitelist.

Первая версия должна:

- различать участников;
- читать контекст;
- держать HOT memory;
- создавать Group Memory Cards;
- отвечать при прямом обращении;
- иногда вмешиваться самостоятельно;
- использовать callbacks;
- поддерживать running jokes;
- делать roast;
- учитывать `profanity_level` и `profanity_frequency` конкретной группы;
- уметь молчать.

---

## Phase 7 — Roast Engine

Выделить отдельную логику:

`opportunity → target → context → callback → running joke → profanity → timing → reply/silence`

Главный принцип:

> Не придумывать шутку из воздуха. Замечать смешное в контексте и добивать.

---

## Phase 8 — Feedback Loop

Собирать сигналы после каждого вмешательства:

Позитивные:

- 😂 / ❤️ / 👍;
- reply;
- повторное обращение;
- organic mention.

Негативные:

- игнор;
- «заткнись»;
- mute;
- remove bot;
- явное недовольство.

Сохранять:

- context;
- reason;
- mode;
- generated text;
- reactions;
- follow-up.

---

## Phase 9 — Friends Test

Продолжительность: 7 дней.

### День 1–2

- initiative: low;
- преимущественно наблюдать;
- собирать карту участников.

### День 3–4

- callbacks: ON;
- initiative: medium;
- начать использовать накопленную память.

### День 5–7

- roast: высокий;
- profanity: по настройке группы;
- adaptive initiative: ON.

Главная Group North Star:

> **Количество участников, кроме владельца, которые сами начали обращаться к НеНою.**

Дополнительные метрики:

- bot mentions;
- replies to bot;
- reactions;
- roast hit rate;
- memory requests;
- reminders;
- ignored interventions;
- negative feedback;
- mute requests;
- bot removed.

---

## Phase 10 — Product Review v0.2

После теста не добавлять новые функции сразу.

Сначала разобрать:

- какие roast попадали;
- какие проваливались;
- какие callbacks были сильными;
- где память ошибалась;
- где НеНой говорил слишком часто;
- какие функции пользователи начали использовать сами.

После этого корректировать:

- prompts;
- thresholds;
- personality parameters;
- mapper;
- context builder;
- initiative.

---

## Phase 11 — Economics & Cost Tracking

Для каждого AI-вызова сохранять:

- model;
- input tokens;
- output tokens;
- cost;
- event type.

Целевые внутренние ориентиры:

- Personal normal: AI COGS < $3 / month;
- Group normal: AI COGS < $3 / month;
- Heavy users/groups: контролируемый верхний диапазон.

Проверять экономику на реальных данных, а не оценках из головы.

---

## Phase 12 — Settings UX

### Personal

Настройки:

- жёсткость;
- юмор;
- подъёб;
- мат;
- инициативность;
- забота;
- память;
- напоминания.

### Group

Администратор задаёт профиль конкретной группы:

- roast;
- sarcasm;
- profanity level;
- profanity frequency;
- initiative;
- callbacks;
- max interventions;
- sensitivity.

Каждая группа имеет свой профиль.

---

## Phase 13 — Closed Beta

Подключить 5–10 групп разных типов:

- друзья;
- семья;
- неформальная команда;
- клуб / сообщество;
- другие естественные групповые чаты.

Проверить, работает ли один Character Engine в разных социальных средах.

---

## Phase 14 — Monetization

Монетизацию проектировать только после подтверждения retention и измерения себестоимости.

Предварительные направления:

- Free;
- Personal;
- Personal Pro;
- Group;
- Personal + Group bundle.

---

## Phase 15 — Platform Expansion

Только после подтверждения core-продукта:

- Voice;
- Telegram Mini App;
- Web UI;
- Calendar;
- Gmail;
- Drive;
- Web Search;
- агенты;
- shared tasks;
- другие мессенджеры.

---

# Что не строим до подтверждения MVP

- отдельное мобильное приложение;
- сложную graph DB;
- десятки агентов;
- сложную тарификацию;
- огромную админку;
- integrations-first архитектуру;
- лишнюю инфраструктуру ради инфраструктуры.

# Critical Path

`Product Vision → Personality Spec → Memory Spec → Dispatcher Spec → Architecture → Personal MVP → Group MVP → Friends Test → Analytics/Economics → v0.2 → Closed Beta → Monetization`
