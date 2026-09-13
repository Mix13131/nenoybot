# ROADMAP — НеНой 2.0

## Phase 0 — Product Vision

**Статус: DONE**

Зафиксирована продуктовая модель: один Core, Personal и Group, Memory Map, Personality Engine, Dispatcher, Context Builder, Action Engine и Analytics.

Артефакт: `PRODUCT_VISION.md`.

---

## Phase 1 — Personality Specification

**Статус: DONE**

Зафиксированы:

- Core Personality;
- Personal Profile;
- Group Profile;
- directness / brevity / warmth / pressure;
- humor / sarcasm / roast;
- profanity level / profanity frequency;
- initiative / callback / challenge / care / playfulness / sensitivity;
- Participant Adaptation;
- Situational Override;
- приоритеты настроек;
- feedback adaptation.

Артефакт: `PERSONALITY_SPEC.md`.

---

## Phase 2 — Memory Specification

**Статус: DONE**

Зафиксированы:

- Personal / Group memory isolation;
- HOT / WARM / LONG memory;
- `Memory Card` schema;
- типы памяти;
- evidence;
- importance / confidence / freshness;
- create / update / merge / supersede / archive / contradict;
- memory relations;
- patterns;
- running jokes и callback fatigue;
- usage policy;
- raw-message retention;
- Memory Mapper contract;
- Retrieval scoring;
- Context budget;
- compaction;
- quality metrics;
- MVP acceptance tests.

Артефакт: `MEMORY_SPEC.md`.

---

## Phase 3 — Dispatcher & Intervention Logic

**Статус: NEXT**

Определить pipeline:

`event → understand → remember? → act? → speak? → mode`

Нужно зафиксировать:

- ignore / remember / reply / act / schedule;
- Event Classification;
- Social Energy detection;
- Intervention Score;
- direct mention priority;
- callback opportunity;
- roast opportunity;
- useful-help opportunity;
- cooldown;
- max proactive interventions;
- serious/conflict override;
- response to ignore / positive / negative feedback;
- связь Dispatcher с Personality Engine и Memory Mapper;
- JSON contracts;
- acceptance tests.

Артефакт: `DISPATCHER_SPEC.md`.

---

## Phase 4 — Technical Architecture

**Статус: PLANNED**

Спроектировать MVP без лишней инфраструктуры.

Компоненты:

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

**Статус: PLANNED**

MVP должен уметь:

- помнить пользователя;
- помнить цели и проекты;
- хранить обещания и решения;
- видеть повторяющиеся паттерны;
- использовать callbacks;
- выбирать Coach / Mirror / Care / Assistant / Observer;
- создавать задачи и напоминания;
- проявлять контролируемую инициативу.

Тест: 7–14 дней реального использования владельцем.

Проверки:

- false memory;
- полезные callbacks;
- неуместные вмешательства;
- ощущение «он меня знает»;
- раздражение;
- качество переключения режимов.

---

## Phase 6 — Group MVP

**Статус: PLANNED**

Подключить один реальный чат друзей через whitelist.

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
- учитывать `profanity_level` и `profanity_frequency` группы;
- уметь молчать.

---

## Phase 7 — Roast Engine

**Статус: PLANNED**

Логика:

`opportunity → target → context → callback → running joke → profanity → timing → reply/silence`

Главный принцип:

> Не придумывать шутку из воздуха. Замечать смешное в контексте и добивать.

---

## Phase 8 — Feedback Loop

**Статус: PLANNED**

Собирать сигналы после каждого вмешательства.

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

Сохранять context, reason, mode, generated text, reactions и follow-up.

---

## Phase 9 — Friends Test

**Статус: PLANNED**

Продолжительность: 7 дней.

### День 1–2

- initiative: low;
- преимущественно наблюдать;
- собирать карту участников.

### День 3–4

- callbacks: ON;
- initiative: medium;
- использовать накопленную память.

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

После теста не добавлять функции автоматически.

Сначала разобрать:

- какие roast попадали;
- какие проваливались;
- какие callbacks были сильными;
- где память ошибалась;
- где НеНой говорил слишком часто;
- какие функции пользователи начали использовать сами.

После этого корректировать prompts, thresholds, personality, mapper, context builder и initiative.

---

## Phase 11 — Economics & Cost Tracking

Для каждого AI-вызова сохранять:

- model;
- input tokens;
- output tokens;
- cost;
- event type.

Внутренние ориентиры:

- Personal normal: AI COGS < $3 / month;
- Group normal: AI COGS < $3 / month;
- Heavy: контролируемый верхний диапазон.

Экономику считать на реальном usage.

---

## Phase 12 — Settings UX

### Personal

- жёсткость;
- юмор;
- подъёб;
- мат;
- инициативность;
- забота;
- память;
- напоминания.

### Group

Администратор задаёт:

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

Проверить Character Engine в разных социальных средах.

---

## Phase 14 — Monetization

Монетизацию проектировать после retention и измерения себестоимости.

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

# Не строим до подтверждения MVP

- отдельное мобильное приложение;
- сложную graph DB;
- десятки агентов;
- сложную тарификацию;
- огромную админку;
- integrations-first архитектуру;
- лишнюю инфраструктуру ради инфраструктуры.

# Critical Path

`Product Vision ✅ → Personality Spec ✅ → Memory Spec ✅ → Dispatcher Spec ← NEXT → Architecture → Personal MVP → Group MVP → Friends Test → Analytics/Economics → v0.2 → Closed Beta → Monetization`
