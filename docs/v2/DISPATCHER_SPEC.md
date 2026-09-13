# DISPATCHER_SPEC v1.0 — НеНой 2.0

## 1. Назначение

Dispatcher — слой принятия решений. Он не пишет финальный текст НеНоя. Его задача — понять событие и вернуть структурированное решение:

- игнорировать;
- сохранить в память;
- ответить;
- выполнить действие;
- запланировать действие;
- вызвать несколько действий одновременно.

Базовый pipeline:

`EVENT → ANALYZE → MEMORY? → ACTION? → SPEAK? → MODE → CONTEXT REQUEST → DECISION`

Главный принцип:

> **НеНой должен иметь такую же способность не вмешиваться, как и способность отвечать.**

---

## 2. Типы входящих событий

Dispatcher принимает не только текстовые сообщения.

Минимальный список событий MVP:

### Telegram message events

- `private_message`
- `group_message`
- `reply_to_bot`
- `direct_mention`
- `command`
- `edited_message`

### Feedback events

- `reaction_added`
- `reaction_removed`
- `reply_to_bot_message`
- `negative_feedback`
- `mute_request`

### Time / scheduler events

- `reminder_due`
- `commitment_due`
- `followup_due`
- `scheduled_support_message`

### Memory events

- `memory_candidate_detected`
- `memory_conflict_detected`
- `memory_expired`
- `pattern_threshold_reached`

В будущем добавляются внешние события Calendar / Gmail / Drive / Web / Agents, но MVP не должен зависеть от них.

---

## 3. Event Envelope

Любое событие приводится к единой структуре.

```json
{
  "event_id": "evt_123",
  "event_type": "group_message",
  "occurred_at": "2026-09-13T16:30:00+03:00",
  "scope_type": "group",
  "scope_id": "tg_group_987",
  "actor_user_id": "tg_user_42",
  "message_id": "tg_msg_1001",
  "reply_to_message_id": null,
  "text": "Я уже выехал",
  "metadata": {}
}
```

Dispatcher не должен зависеть от Telegram-формата напрямую. Telegram Adapter сначала нормализует событие в Event Envelope.

---

## 4. Анализ сцены

До принятия решения Dispatcher получает компактную оценку ситуации.

```json
{
  "direct_mention": false,
  "reply_to_bot": false,
  "question_to_bot": false,
  "command_intent": null,

  "banter_score": 0.91,
  "seriousness_score": 0.08,
  "conflict_score": 0.12,
  "sensitivity_score": 0.05,

  "roast_opportunity": 0.88,
  "callback_opportunity": 0.81,
  "help_opportunity": 0.10,
  "memory_value": 0.22,

  "contradiction_score": 0.76,
  "commitment_signal": 0.02,
  "decision_signal": 0.01
}
```

Оценки `0..1` не являются абсолютной истиной. Это сигналы для policy layer.

---

## 5. Primary Actions

Dispatcher обязан вернуть ровно один `primary_action`:

- `ignore`
- `reply`
- `act`
- `schedule`

Memory update не обязан быть primary action и чаще выступает как secondary action.

### Secondary Actions

Допустимые значения MVP:

- `remember`
- `update_memory`
- `link_memory`
- `record_feedback`
- `create_task`
- `create_reminder`
- `reschedule`
- `cancel_reminder`

Пример: сообщение может одновременно получить `primary_action=reply` и `secondary_actions=[remember]`.

---

## 6. Жёсткие правила до Intervention Score

Некоторые события не должны проходить через обычный scoring.

### 6.1 Прямое обращение

Если пользователь явно обращается к НеНою, по умолчанию:

`reply = true`

Исключения:

- служебная команда, которую нужно выполнить без разговорного ответа;
- duplicate/retry event;
- техническая ошибка, обработанная отдельно.

### 6.2 Явная команда памяти

Примеры:

- «НеНой, запомни…»
- «забудь это»
- «это уже не актуально»
- «не используй это для подъёба»

Такие команды имеют приоритет над автоматическим Memory Mapper.

### 6.3 Явное действие

Примеры:

- «напомни завтра…»
- «отмени напоминание…»
- «зафиксируй задачу…»

Dispatcher маршрутизирует их в Action Engine.

---

## 7. Intervention Score — Group Mode

Для самостоятельного вмешательства в групповой чат используется `intervention_score` диапазона `0..100`.

Стартовые веса для MVP:

| Signal | Weight |
|---|---:|
| direct mention | +100 |
| reply to bot | +100 |
| question to bot | +100 |
| strong callback opportunity | +35 |
| explicit contradiction | +30 |
| broken / due commitment relevant to scene | +25 |
| strong roast opportunity | +25 |
| useful factual/help opportunity | +20 |
| active running joke with strong fit | +20 |
| conversation explicitly about bot | +15 |
| bot spoke recently | -35 |
| previous unsolicited message ignored | -20 |
| two unsolicited messages ignored recently | -35 |
| serious conflict | -60 |
| sensitive / vulnerable context | -70 |
| direct request to stay quiet | -100 |
| group temporarily muted bot | -100 |

Веса — стартовые. После первого Friends Test они должны быть откалиброваны по данным.

### Thresholds

Для unsolicited Group intervention:

- `< 60` → `ignore`
- `60..74` → вмешательство возможно только если initiative profile высокий и cooldown свободен
- `>= 75` → `reply`, если нет hard blocker

Прямые обращения не используют этот threshold.

---

## 8. Intervention Score не должен быть просто суммой

Некоторые сигналы должны работать как gates.

Пример:

```text
roast_opportunity = high
BUT
seriousness = critical
→ no roast
```

Поэтому логика:

1. hard blockers;
2. hard intents;
3. context gates;
4. score;
5. cooldown;
6. rate limits;
7. mode selection.

---

## 9. Silence Policy

`ignore` — не ошибка и не fallback. Это нормальный продуктовый результат.

Group Mode должен по умолчанию молчать, если:

- обычная бытовая переписка без сильного повода;
- шутка посредственная;
- контекст неясный;
- бот недавно уже вмешивался;
- участники продолжают разговор без необходимости помощи;
- callback слабый или притянутый;
- ответ будет повторять уже сказанное;
- ситуация чувствительная и участие не было запрошено.

Принцип:

> **Лучше пропустить хороший потенциальный комментарий, чем системно быть лишним.**

---

## 10. Cooldown — Group Mode

Стартовые параметры MVP:

```json
{
  "unsolicited_min_cooldown_minutes": 12,
  "soft_daily_limit": 6,
  "hard_daily_limit": 10
}
```

Это относится только к самостоятельным вмешательствам. Прямые обращения пользователей не блокируются обычным cooldown.

### Adaptive cooldown

После положительного feedback:

- cooldown не снижается резко;
- можно медленно повышать effective initiative.

После негативного feedback:

- cooldown увеличивается сразу;
- initiative уменьшается быстрее.

Пример:

```text
2 unsolicited interventions подряд без реакции
→ cooldown × 1.5

явное «заткнись»
→ silent_until + 2h (или настройка группы)
```

Конкретные коэффициенты валидируются тестом.

---

## 11. Activity-aware behavior

НеНой должен учитывать скорость чата.

### Slow chat

При 5 сообщениях за час unsolicited intervention требует более сильного повода.

### Fast chat

При 50 сообщениях за 10 минут абсолютное число сообщений НеНоя может быть выше, но его доля в разговоре должна оставаться низкой.

Вводим показатель:

`bot_share_last_window`

Ориентир для MVP:

> unsolicited messages НеНоя не должны стабильно превышать ~5–10% сообщений активной сцены.

Это не жёсткая бизнес-константа, а guardrail.

---

## 12. Personal Mode Decision Policy

В Personal Mode логика другая: пользователь пришёл к НеНою именно разговаривать, поэтому `reply` — стандартный результат сообщения.

Dispatcher решает прежде всего:

- какой режим использовать;
- нужно ли обновлять память;
- нужно ли создать action/reminder;
- стоит ли запланировать proactive follow-up.

### Mode candidates

- `assistant`
- `coach`
- `mirror`
- `care`
- `observer`
- `execution`

Пример сигналов:

```text
прямой практический вопрос
→ assistant

отмазка + активная цель
→ coach / mirror

усталость / перегруз / просьба не давить
→ care

обещание / deadline / task
→ execution
```

Режим не должен определяться одним ключевым словом; учитывается recent context + relevant memory.

---

## 13. Proactive Personal Interventions

Личный НеНой может инициировать взаимодействие, но не превращаться в push-спам.

Типовые причины:

- наступил deadline;
- пользователь сам попросил напомнить;
- открытый commitment давно без статуса;
- запланированный support message;
- значимая цель осталась без движения и есть заранее разрешённая инициативность.

Стартовый guardrail:

- не более 2–4 proactive сообщений в день без явных reminders;
- пользовательский reminder не считается unsolicited intervention;
- негативная реакция снижает proactive frequency.

---

## 14. Mode Selection — Group

Допустимые режимы ответа MVP:

- `group_direct_reply`
- `group_banter`
- `group_roast`
- `group_callback`
- `group_help`
- `group_organizer`
- `group_arbiter`

Пример выбора:

```text
strong roast + banter high + sensitivity low
→ group_roast

callback high, roast medium
→ group_callback

прямой вопрос о договорённости
→ group_arbiter / group_help

дата / встреча / reminder
→ group_organizer
```

---

## 15. Roast Gate

Перед `group_roast` должны пройти все условия:

1. `roast_opportunity` достаточно высокий;
2. scene не serious/sensitive;
3. group `roast_level > 0`;
4. target participant не имеет сильного negative adaptation;
5. callback/running joke не находится в fatigue state;
6. group cooldown позволяет вмешательство;
7. roast не создаёт коалицию одного участника против другого без контекста.

Если gate не проходит, Dispatcher выбирает другой mode или silence.

---

## 16. Callback Gate

Callback используется только если:

- relevant memory confidence выше порога;
- memory не contradicted / archived;
- visibility policy разрешает использование в текущем scope;
- callback действительно связан с текущим сообщением;
- memory не использовалась слишком часто недавно.

Для спорных фактов лучше использовать формулировку с подтверждением источником, чем уверенный вымысел.

---

## 17. Memory Decision

Dispatcher не обязан сам строить Memory Card, но определяет необходимость Memory Mapper.

Минимальные результаты:

- `memory_action = none`
- `memory_action = candidate`
- `memory_action = explicit_write`
- `memory_action = update_candidate`
- `memory_action = explicit_forget`

### Высокая вероятность сохранения

- обещание;
- решение;
- deadline;
- долгосрочная цель;
- повторяющийся паттерн;
- явная пользовательская команда «запомни»;
- сильный running joke;
- важное изменение ранее сохранённого факта.

### Низкая вероятность сохранения

- «ага»;
- одноразовая шутка;
- реакция без самостоятельного смысла;
- случайный бытовой факт без будущей ценности.

---

## 18. Action Decision

Если обнаружен action intent, Dispatcher формирует структурированный запрос Action Engine.

Пример:

```json
{
  "action_type": "create_reminder",
  "actor_user_id": "tg_user_42",
  "scope_id": "tg_group_987",
  "payload": {
    "text": "Заказать баню",
    "due_at": "2026-09-14T18:00:00+03:00"
  }
}
```

Action Engine отвечает успехом/ошибкой, после чего Response Generator при необходимости формирует человеческий ответ.

---

## 19. Dispatcher Decision Contract

Базовый JSON-контракт:

```json
{
  "decision_id": "dec_123",
  "event_id": "evt_123",

  "primary_action": "reply",
  "secondary_actions": ["remember"],

  "mode": "group_roast",
  "target_user_id": "tg_user_42",

  "intervention_score": 84,
  "reason_codes": [
    "strong_callback",
    "high_roast_opportunity",
    "banter_context"
  ],

  "memory_action": "candidate",
  "memory_queries": [
    "running_joke:already_on_the_way",
    "recent_commitments"
  ],

  "personality_overrides": {
    "roast": 2,
    "callback": 1
  },

  "action_request": null,

  "cooldown": {
    "allowed": true,
    "next_unsolicited_after": "2026-09-13T16:42:00+03:00"
  },

  "debug": {
    "policy_version": "dispatcher-v1"
  }
}
```

Для production `debug` можно не передавать в Response Generator, но сохранять для аналитики.

---

## 20. Reason Codes

Чтобы потом понимать, почему система вмешалась, решение должно иметь не свободный текст, а стабильные reason codes.

Стартовый набор:

- `direct_mention`
- `reply_to_bot`
- `question_to_bot`
- `explicit_command`
- `strong_callback`
- `contradiction`
- `broken_commitment`
- `roast_opportunity`
- `running_joke_match`
- `help_opportunity`
- `organizer_intent`
- `reminder_due`
- `care_context`
- `coach_context`
- `serious_context_block`
- `sensitive_context_block`
- `cooldown_block`
- `daily_limit_block`
- `low_relevance`
- `recent_ignore_penalty`
- `user_requested_silence`

Reason codes обязательны для analytics и последующей настройки thresholds.

---

## 21. Deterministic vs LLM Logic

Не всё должен решать LLM.

### Deterministic layer

- direct mention;
- reply-to-bot;
- explicit mute/silence;
- cooldown;
- daily limits;
- scope privacy;
- explicit reminder commands after intent extraction;
- duplicate event handling.

### Lightweight model / classifier

- seriousness;
- banter;
- conflict;
- roast opportunity;
- callback opportunity;
- mode candidates;
- memory value.

### Strong generation model

Используется только после решения `reply` и после сборки Context Package.

Принцип:

> **Дорогая модель не должна решать, стоит ли вообще вызывать дорогую модель.**

---

## 22. Cost-aware routing

Dispatcher обязан отдавать `model_tier`:

- `none`
- `light`
- `standard`
- `strong`

Пример:

```text
ignore
→ none

memory extraction
→ light

простая фактическая реплика
→ standard

тонкий Personal Mirror / сложный roast / nuanced conflict
→ strong
```

Фактическая привязка к конкретным моделям задаётся конфигурацией, а не зашивается в Dispatcher Spec.

---

## 23. Failure Policy

### Если classifier недоступен

- прямые обращения продолжают обслуживаться fallback policy;
- unsolicited Group intervention отключается;
- память можно временно не обновлять, чем записывать мусор.

### Если Memory Retrieval недоступен

- не использовать callback/contradiction claims;
- ответить без ложной ссылки на прошлое.

### Если strong model недоступна

- fallback на standard model для простых ответов;
- сложный proactive roast лучше пропустить.

Принцип:

> **При деградации система должна становиться тише и осторожнее, а не фантазировать.**

---

## 24. Feedback Loop

После сообщения НеНоя создаётся `intervention` record.

Сохраняем:

- event_id;
- decision_id;
- mode;
- reason_codes;
- intervention_score;
- context features;
- model tier;
- output text;
- timestamp.

Позже привязываем feedback:

- reaction;
- reply;
- organic follow-up;
- ignore;
- negative feedback;
- mute;
- removal.

Это позволит анализировать не только качество текста, но и качество самого решения «вмешиваться или нет».

---

## 25. Group Examples

### Example A — обычная переписка

```text
Серёга: Кто хлеб купит?
Лёха: Я зайду.
```

Нет direct mention, нет сильного callback, нет полезной роли.

```json
{
  "primary_action": "ignore",
  "reason_codes": ["low_relevance"]
}
```

### Example B — сильный callback

Memory: Серёга регулярно пишет «уже еду», ещё находясь дома.

Current:

```text
Серёга: Уже выехал.
```

Banter high, callback high, cooldown free.

```json
{
  "primary_action": "reply",
  "mode": "group_callback",
  "reason_codes": ["strong_callback", "running_joke_match"]
}
```

### Example C — тот же callback, но бот уже недавно шутил

```json
{
  "primary_action": "ignore",
  "reason_codes": ["cooldown_block"]
}
```

### Example D — настоящий конфликт

```text
— Ты меня заебал врать.
— Отстань от меня.
```

Даже при формальном contradiction signal:

```json
{
  "primary_action": "ignore",
  "reason_codes": ["serious_context_block"]
}
```

Если потом участник явно спрашивает НеНоя о фактах, можно ответить в `group_arbiter` без roast.

---

## 26. Personal Examples

### Example A — практический вопрос

```text
Антон: Напомни завтра в 11 написать поставщику.
```

```json
{
  "primary_action": "act",
  "secondary_actions": ["create_reminder"],
  "mode": "execution",
  "reason_codes": ["explicit_command", "organizer_intent"]
}
```

### Example B — повторяющаяся отмазка

Active goal + relevant pattern memory.

```text
Антон: Я ещё чуть-чуть подготовлюсь и потом запущу.
```

```json
{
  "primary_action": "reply",
  "mode": "mirror",
  "reason_codes": ["coach_context", "strong_callback"]
}
```

### Example C — пользователь просит не давить

```text
Сегодня просто не дави на меня.
```

Dispatcher sets temporary context override and selects `care`.

---

## 27. MVP Configuration

Все thresholds и limits должны жить в конфигурации, а не быть разбросаны по коду.

Минимальный config:

```json
{
  "group": {
    "intervention_threshold": 75,
    "borderline_threshold": 60,
    "unsolicited_min_cooldown_minutes": 12,
    "soft_daily_limit": 6,
    "hard_daily_limit": 10
  },
  "personal": {
    "proactive_soft_daily_limit": 3
  },
  "memory": {
    "candidate_threshold": 0.65
  }
}
```

После Friends Test значения меняются без переписывания бизнес-логики.

---

## 28. Что измеряем у Dispatcher

### Group

- unsolicited intervention count;
- intervention acceptance rate;
- reaction rate;
- reply rate;
- ignore-after-bot rate;
- negative feedback rate;
- mute rate;
- reason-code performance;
- score bucket performance;
- cooldown blocks;
- false positive interventions;
- missed opportunities (ручная выборка на тесте).

### Personal

- correct mode selection;
- proactive response rate;
- reminder/action completion;
- user correction of bot interpretation;
- unnecessary pressure / unnecessary care cases.

---

## 29. Success Criteria v1

Dispatcher v1 считается рабочим для MVP, если:

1. прямые обращения почти всегда корректно маршрутизируются;
2. unsolicited Group interventions редкие и объяснимые reason codes;
3. серьёзные сцены подавляют roast;
4. cooldown реально предотвращает навязчивость;
5. память и actions могут запускаться без обязательного ответа;
6. любой ответ можно ретроспективно объяснить: почему НеНой полез в разговор;
7. при отказе части инфраструктуры Group Mode становится тише, а не опаснее;
8. thresholds можно менять конфигурацией без переписывания core logic.

---

## 30. Следующий документ

После фиксации Dispatcher Spec переходим к `ARCHITECTURE.md`.

Там Dispatcher превращается из логики в реальные компоненты, очереди/воркеры, таблицы, сервисы, model routing, scheduler и end-to-end pipeline v2.
