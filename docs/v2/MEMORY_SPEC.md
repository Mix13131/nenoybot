# MEMORY_SPEC v1.0 — НеНой 2.0

## 1. Зачем нужна память

Память НеНоя — не архив переписки и не попытка постоянно передавать модели всю историю сообщений.

Цель памяти:

> **превращать большой поток общения в компактную, проверяемую и актуальную карту контекста.**

НеНой должен помнить достаточно, чтобы:

- понимать, кто есть кто;
- помнить цели, проекты, решения и обещания;
- видеть повторяющиеся паттерны;
- доставать уместные callbacks;
- поддерживать running jokes в группе;
- понимать, что изменилось;
- не задавать одни и те же вопросы;
- не таскать десятки тысяч старых сообщений в каждый LLM-запрос.

Главный принцип:

> **Сообщения — это сырьё. Memory Map — это знание.**

---

## 2. Что память НЕ должна делать

Memory System не должна:

- сохранять каждое сообщение как вечную «память»;
- считать любую фразу пользователя вечной истиной;
- превращать догадки модели в факты;
- бесконечно накапливать дубликаты одного и того же знания;
- автоматически переносить Personal Memory в Group Memory;
- отправлять модели всю БД ради одного ответа;
- использовать старый факт, если более новый факт его отменил;
- бесконечно повторять один и тот же callback или running joke.

---

## 3. Memory Scopes

Память всегда существует внутри явного scope.

### PERSONAL

```text
scope_type = personal
scope_id = telegram_user_id
```

Содержит только личный контекст человека и его диалог с НеНоем.

### GROUP

```text
scope_type = group
scope_id = telegram_chat_id
```

Содержит только события, возникшие внутри конкретной группы.

### Жёсткое правило изоляции

Group Engine не получает Personal Memory участника при построении группового контекста.

Это ограничение должно обеспечиваться запросом к данным, а не только системным промптом.

Пример:

```sql
WHERE scope_type = 'group'
  AND scope_id = :current_chat_id
```

Личная информация не должна оказаться в выборке вообще.

---

## 4. Три горизонта памяти

### 4.1 HOT MEMORY

Короткий текущий контекст разговора.

Содержит:

- последние сообщения;
- ответы/replies;
- текущих участников сцены;
- активную тему;
- недавно вызванные действия.

MVP default:

```text
max_messages = 100
max_tokens ≈ 4000
```

Работа идёт по токен-бюджету, а не только по числу сообщений.

HOT используется непосредственно Context Builder.

### 4.2 WARM MEMORY

Недавняя история, которая ещё может потребоваться для восстановления контекста или построения Memory Cards.

Содержит:

- сырые сообщения за ограниченный период;
- краткие thread/day summaries;
- недавно закрытые обещания;
- свежие решения;
- события последних дней/недель.

MVP default raw retention:

```text
30 дней
```

После TTL текст обычных сообщений может быть удалён/очищен, если он не используется как evidence для долгосрочной памяти.

### 4.3 LONG MEMORY

Компактная структурная память.

Содержит Memory Cards и связи между ними.

Именно LONG Memory создаёт ощущение:

> «Он реально помнит и понимает историю».

LONG Memory не ограничивается 30 днями, но каждая карточка имеет freshness, confidence, status и правила устаревания.

---

## 5. Базовая единица — Memory Card

Memory Card — короткий смысловой объект, а не копия сообщения.

Пример:

```json
{
  "id": "mem_01J...",
  "scope_type": "group",
  "scope_id": "-100123456789",
  "type": "running_joke",
  "subject_keys": ["user:sergey"],
  "summary": "Фраза «уже еду» у Серёги часто появляется до фактического выезда.",
  "payload": {
    "trigger_phrase": "уже еду",
    "evidence_count": 5
  },
  "importance": 0.72,
  "confidence": 0.94,
  "freshness": 0.88,
  "status": "active",
  "origin": "inferred",
  "pinned": false,
  "usage_policy": {
    "assist": true,
    "callback": true,
    "roast": true,
    "proactive": true
  },
  "evidence": [
    {
      "message_id": 15537,
      "author_id": 112233,
      "timestamp": "2026-09-13T18:41:00+03:00",
      "excerpt": "Уже еду"
    }
  ],
  "created_at": "...",
  "updated_at": "...",
  "last_confirmed_at": "...",
  "last_used_at": "..."
}
```

`summary` должен быть коротким и достаточным для передачи модели без исходной переписки.

---

## 6. Обязательные поля Memory Card

### Identity

- `id`
- `scope_type`
- `scope_id`
- `type`
- `subject_keys[]`

### Content

- `summary`
- `payload JSONB`

### Quality

- `importance: 0..1`
- `confidence: 0..1`
- `freshness: 0..1`

### Lifecycle

- `status`
- `origin`
- `pinned`
- `created_at`
- `updated_at`
- `last_confirmed_at`
- `last_used_at`
- `valid_from`
- `valid_until`
- `superseded_by`

### Evidence

- `evidence[]`
- `source_count`

### Usage

- `usage_policy`

---

## 7. Statuses

Минимальный набор:

```text
candidate
active
archived
superseded
rejected
```

### `candidate`

Потенциальная память, которой пока недостаточно доказательств.

Например:

> Серёга один раз опоздал.

Это ещё не pattern.

### `active`

Актуальная память, доступная Retrieval Engine.

### `archived`

Историческая память. По умолчанию не попадает в обычный context retrieval.

### `superseded`

Заменена более новым знанием.

### `rejected`

Memory Mapper решил, что кандидат был ошибочным/неподтверждённым.

---

## 8. Origins

```text
explicit_user
explicit_group
inferred
system_action
imported
```

### Explicit memory

Если пользователь прямо говорит:

> «НеНой, запомни: встреча в субботу в 18:00»

это имеет приоритет над inferred memory.

### Inferred memory

Любой вывод, который сделал Memory Mapper сам, должен иметь evidence и более осторожный confidence.

---

## 9. Типы Memory Cards

### `person`

Что известно о конкретном участнике в данном scope.

Не создавать психологические досье. Хранить только контекст, реально полезный продукту.

### `fact`

Конкретный проверяемый факт.

### `goal`

Желаемый результат пользователя.

### `project`

Длительный проект/направление работы.

### `task`

Связь с задачей. Рабочее состояние самой задачи лучше хранить в `tasks`, а Memory Card использовать как контекст.

### `commitment`

Обещание/обязательство человека.

Пример:

> «Я завтра забронирую баню».

### `decision`

Принятое решение.

### `plan`

Будущий план без жёсткого обязательства.

### `event`

Произошедшее событие.

### `preference`

Устойчивое предпочтение, если оно явно сказано или подтверждено повторно.

### `observation`

Полезное наблюдение без достаточных оснований называть его pattern.

### `pattern`

Повторяющееся поведение, подтверждённое несколькими независимыми evidence points.

### `running_joke`

Локальный мем/повторяющийся прикол группы.

### `quote`

Дословная короткая цитата, ценная для дальнейшего callback.

Обязателен точный evidence excerpt.

### `contradiction`

Связь между двумя утверждениями/действиями, которые конфликтуют друг с другом.

### `relationship`

Связь между сущностями: человек ↔ проект, человек ↔ обещание, событие ↔ решение и т.п.

---

## 10. Что НЕ стоит сохранять как Memory Card

Примеры:

```text
«ага»
«ок»
«доброе утро»
одноразовая шутка без продолжения
случайная эмоция
вопрос без решения
обычный small talk
одноразовое опоздание
неподтверждённая догадка модели
```

База не должна становиться свалкой.

---

## 11. Memory Relations

Связи позволяют получить карту без graph database.

MVP — обычная таблица `memory_relations`.

Пример полей:

```text
id
scope_type
scope_id
from_card_id
relation_type
to_card_id
weight
created_at
```

Базовые relation types:

```text
ABOUT
RELATED_TO
BELONGS_TO
PART_OF
PROMISED_BY
DECIDED_BY
INVOLVES
CONTRADICTS
SUPERSEDES
DERIVED_FROM
SUPPORTS
RUNNING_JOKE_ABOUT
```

Отдельная graph DB для MVP не нужна.

---

## 12. Entity Keys

Для связей нужен стабильный идентификатор сущности.

Примеры:

```text
user:123456789
group:-100123456789
project:content_hub
goal:launch_v2
task:uuid
memory:uuid
```

Имена людей не должны использоваться как уникальные ключи.

---

## 13. Evidence

Memory Card должна уметь объяснить, почему она существует.

Evidence содержит минимум:

- Telegram `message_id`;
- `author_id`;
- timestamp;
- короткий excerpt;
- optional relation to another Memory Card.

Evidence excerpt должен быть коротким — только достаточная фраза, а не огромный кусок чата.

После удаления WARM raw text карточка продолжает иметь доказательство через excerpt.

---

## 14. Confidence

`confidence` отвечает на вопрос:

> Насколько мы уверены, что карточка корректно описывает реальность?

Стартовые ориентиры:

```text
явная команда «запомни»        0.98
явное утверждение пользователя 0.95
явное решение группы           0.95
два подтверждения              0.85
несколько повторений           0.90+
модельный вывод                <= 0.70 до подтверждения
```

Нельзя превращать inference в уверенный факт только потому, что LLM красиво сформулировала вывод.

---

## 15. Importance

`importance` отвечает:

> Насколько эта память полезна для будущего поведения НеНоя?

Высокий importance:

- ключевая цель;
- важное решение;
- активное обещание;
- явная пользовательская настройка;
- устойчивый pattern;
- сильный running joke;
- важная граница («эту тему не использовать для roast»).

Низкий importance:

- мелкая деталь одноразового события;
- слабое наблюдение;
- устаревшая информация.

---

## 16. Freshness и decay

Freshness — не «правда/ложь», а актуальность для текущего контекста.

Пример расчёта:

```text
freshness = exp(-age / half_life)
```

Точная формула может измениться после тестов.

MVP half-life ориентиры:

| Type | Half-life |
|---|---:|
| identity / hard setting | 730 дней |
| pattern | 365 дней |
| quote | 365 дней |
| preference | 180 дней |
| project | 180 дней |
| decision | 180 дней |
| running_joke | 90 дней |
| observation | 60 дней |
| event | 30 дней |

`pinned = true` не означает «вечная истина», но отключает автоматическое архивирование без явного пересмотра.

---

## 17. Confirmation refresh

Если новый evidence подтверждает существующую карточку:

- увеличиваем `source_count`;
- обновляем `last_confirmed_at`;
- повышаем confidence в допустимых пределах;
- восстанавливаем freshness;
- не создаём новую карточку-дубликат.

Пример:

```text
Серёга снова пишет «уже еду» до выезда.
```

Нужно обновить существующий `running_joke/pattern`, а не создать шестой одинаковый объект.

---

## 18. Merge / Compaction

Memory System должна сжимать знания.

Пример входа:

```text
1. Боится выпускать сырое.
2. Перед запуском начинает дополнительную подготовку.
3. Переписывает готовое перед публикацией.
4. Ищет ещё одно подтверждение перед стартом.
```

После накопления evidence Memory Compactor может создать:

```text
PATTERN:
Перед запуском склонен увеличивать объём подготовки даже после достижения достаточной готовности MVP.
```

Мелкие карточки:

- становятся `archived`;
- связываются `DERIVED_FROM` / `SUPPORTS`;
- остаются доступными для аудита, но не участвуют в обычном retrieval.

---

## 19. Soft cap вместо бесконечного active memory

База технически может хранить много карточек, но активный рабочий набор должен быть компактным.

MVP soft caps:

```text
Personal active LONG cards ≈ 150
Group active LONG cards ≈ 250
```

Это не hard delete limit.

При превышении soft cap запускается compaction:

1. найти дубликаты;
2. объединить сходные observation/pattern;
3. архивировать завершённые/устаревшие объекты;
4. создать summary cards более высокого уровня;
5. сохранить pinned и явно важные карточки.

Главная цель — не маленькая БД сама по себе, а маленький **активный смысловой слой**.

---

## 20. Contradiction Handling

НеНой не должен молча переписывать прошлое.

Пример:

```text
11 сентября: «Я больше не пью»
13 сентября: «Закажем ещё по одной?»
```

Возможная структура:

- сохранить обе evidence points;
- создать `contradiction`;
- не удалять автоматически старое утверждение;
- уменьшить confidence старой preference/commitment, если новое действие явно её опровергает.

Если пользователь говорит:

> «Нет, планы поменялись, теперь делаем B»

то старое решение:

```text
status = superseded
superseded_by = new_card_id
```

---

## 21. Patterns

Pattern нельзя создавать по одному случаю.

MVP rule of thumb:

```text
минимум 3 evidence points
минимум 2 разных временных эпизода
```

Для сильного явного поведения возможны исключения, но confidence остаётся ниже до подтверждений.

Pattern должен описывать наблюдаемое поведение, а не ставить человеку психологический диагноз.

Плохо:

> «Серёга безответственный».

Хорошо:

> «Серёга несколько раз называл время выезда раньше фактического выезда».

---

## 22. Running Jokes

Running joke — отдельный тип, потому что он критичен для Group Mode.

Создаётся не из одной шутки, а когда есть признаки повторяемости:

- тема появилась несколько раз;
- участники сами к ней возвращаются;
- шутка получает реакции;
- callback был успешным.

Поля payload могут включать:

```json
{
  "trigger": "уже еду",
  "target_user_id": 123,
  "hit_count": 7,
  "successful_callbacks": 4,
  "last_callback_at": "..."
}
```

### Callback fatigue

Один и тот же прикол нельзя использовать постоянно.

Retrieval должен снижать score, если карточка недавно использовалась.

Пример:

```text
callback_fatigue_penalty = high,
если same joke использован < 3 дней назад
```

Удачный running joke должен ощущаться редким callback, а не кнопкой, которую бот нажимает каждый час.

---

## 23. Usage Policy

Не вся память может использоваться для любой цели.

Пример:

```json
{
  "assist": true,
  "coach": true,
  "callback": false,
  "roast": false,
  "proactive": true
}
```

Это позволяет сохранить важную информацию, но не использовать её как материал для подъёба.

Для Group Mode особенно важно право участника сказать:

> «Эту тему не используй для roast».

Тогда соответствующая Memory Card получает:

```text
usage_policy.roast = false
```

---

## 24. User correction / forget controls

НеНой обязан понимать естественные команды:

```text
«НеНой, это уже не актуально»
«Забудь это»
«Исправь: теперь ...»
«Не используй это для подъёба»
«Это неправда»
```

Действия:

- `archive`;
- `supersede`;
- `delete`;
- update usage policy;
- lower confidence;
- create corrected card.

Явная коррекция пользователя имеет более высокий приоритет, чем inference модели.

---

## 25. Raw Message Retention

Таблица `messages` нужна для HOT/WARM контекста, аналитики и memory extraction.

MVP:

```text
0–30 дней: хранится raw text
>30 дней: обычный text очищается или удаляется
```

Можно оставить техническую metadata:

- message_id;
- chat_id;
- author_id;
- timestamp;
- reply_to_message_id;
- content type;
- optional hash.

Если сообщение является evidence для Memory Card, достаточный короткий excerpt хранится внутри evidence.

Политика retention должна быть конфигурируемой до публичного запуска.

---

## 26. Memory Mapper pipeline

Не каждое сообщение должно вызывать дорогой memory-анализ.

Рекомендуемый pipeline:

```text
Telegram Event
    ↓
Cheap event classification
    ↓
Immediate path OR deferred path
```

### Immediate path

Обрабатывается сразу, если найдено:

- «запомни»;
- задача;
- напоминание;
- обещание;
- решение;
- явное изменение настройки;
- прямое исправление памяти.

### Deferred path

Обычные сообщения обрабатываются батчами.

MVP triggers:

```text
каждые ~20 новых сообщений
или
10 минут тишины после активного диалога
или
периодический background compaction
```

Это уменьшает стоимость и позволяет оценивать паттерны не по одной реплике, а по сцене.

---

## 27. Mapper Output Contract

Memory Mapper возвращает структурированный результат.

Пример:

```json
{
  "actions": [
    {
      "action": "update",
      "card_type": "running_joke",
      "match_key": "already_on_way_sergey",
      "summary": "Фраза «уже еду» у Серёги часто появляется до фактического выезда.",
      "importance": 0.74,
      "confidence": 0.93,
      "evidence_message_ids": [18221],
      "relations": [],
      "usage_policy": {
        "callback": true,
        "roast": true,
        "proactive": true
      }
    }
  ]
}
```

Допустимые `action`:

```text
ignore
create
update
merge
supersede
archive
contradict
```

---

## 28. Memory Retrieval

Context Builder не забирает все active cards.

Сначала формируется candidate set по структуре:

1. текущий scope;
2. текущие участники;
3. текущая тема;
4. активные goals/projects/commitments;
5. тип текущего режима;
6. recent relevance.

После этого рассчитывается retrieval score.

Черновая формула:

```text
score =
  relevance      * 0.35
+ importance     * 0.20
+ confidence     * 0.15
+ freshness      * 0.15
+ mode_fit       * 0.10
+ novelty        * 0.05
- fatigue_penalty
```

Весы должны настраиваться после тестов.

---

## 29. Retrieval by mode

### PERSONAL / COACH

Приоритет:

- active goals;
- commitments;
- recent decisions;
- patterns;
- unresolved tasks.

### PERSONAL / CARE

Приоритет:

- текущий контекст;
- предпочтения по поддержке;
- недавние observations;
- без агрессивного callback retrieval.

### GROUP / ROAST

Приоритет:

- target participant;
- current message;
- contradictions;
- running jokes;
- quotes;
- recent promises;
- callback fatigue penalty.

### GROUP / ORGANIZER

Приоритет:

- decisions;
- plans;
- commitments;
- dates/events;
- без лишнего roast context.

---

## 30. Context Budget

Память должна экономить токены.

MVP soft budget на один generation request:

```text
HOT conversation context: до ~3000–4000 tokens
WARM summary/context:    до ~500–800 tokens
LONG Memory Cards:      до ~800–1200 tokens
```

Обычно генератор получает 3–8 наиболее релевантных Memory Cards, а не сотни.

Задача Context Builder:

> дать модели минимальный пакет истины, достаточный для хорошего ответа.

---

## 31. Embeddings / vector search

Для MVP отдельный vector stack не обязателен.

Первая версия может использовать:

- structured filtering;
- JSONB;
- PostgreSQL full-text search;
- subject/type/status filters;
- recency.

Архитектура Retrieval Engine должна позволять позже добавить `pgvector`, не меняя формат Memory Card.

Vector search становится полезнее после роста active memory и количества scopes, но не является условием запуска Friends MVP.

---

## 32. Memory Compactor

Фоновая задача выполняет:

1. deduplication;
2. merge похожих карточек;
3. confidence refresh;
4. decay freshness;
5. archive stale cards;
6. создание higher-level patterns;
7. callback fatigue maintenance;
8. проверку active-memory soft cap.

Рекомендуемый MVP schedule:

```text
1 раз в сутки для Personal
1 раз в сутки для активных Groups
+ on-demand при превышении soft cap
```

---

## 33. False Memory Protection

Ключевой риск продукта — уверенно вспоминать то, чего не было.

Правила:

1. Дословная цитата требует exact evidence excerpt.
2. «Ты говорил X» требует explicit evidence или высокой confidence.
3. Pattern нельзя выдавать как абсолютный факт.
4. Inferred card не должна получать confidence 1.0.
5. При конфликтующих evidence НеНой показывает неопределённость, а не выбирает удобную версию.
6. Если evidence слабое, формулировка должна быть мягче: «кажется», «несколько раз замечал», «могу ошибаться».
7. Memory Card без достаточного evidence не используется для жёсткого roast.

---

## 34. Group-specific privacy

В Group scope разрешено использовать только информацию:

- написанную в этой группе;
- явно добавленную в эту группу командой;
- созданную из действий НеНоя внутри этой группы.

Не допускается:

```text
Personal → Group automatic lookup
Group A → Group B automatic lookup
```

Даже если это один и тот же Telegram user.

---

## 35. Group participant boundaries

Участник группы может задать собственную границу внутри группы:

```text
«Не используй тему X в шутках про меня»
```

Это создаёт/обновляет group-scoped preference/usage restriction для этого участника.

Администратор может менять профиль группы, но не должен автоматически отменять индивидуальные ограничения участника на использование конкретной темы для roast.

---

## 36. Telegram limitation

Бот не получает магический доступ ко всей истории группы до момента добавления.

На старте Group Mode НеНой знает:

- сообщения, которые Telegram передал после подключения;
- явно сообщённый контекст;
- данные, созданные внутри текущего Group scope.

Поэтому первые дни теста естественно являются фазой знакомства и построения карты.

---

## 37. Recommended DB mapping

### `messages`

HOT/WARM raw events.

### `memory_cards`

LONG semantic knowledge.

Рекомендуемые колонки:

```text
id UUID PK
scope_type
scope_id
card_type
summary
payload JSONB
subject_keys JSONB
importance REAL
confidence REAL
freshness REAL
status
origin
pinned BOOLEAN
usage_policy JSONB
evidence JSONB
source_count INTEGER
valid_from
valid_until
last_confirmed_at
last_used_at
superseded_by
created_at
updated_at
```

### `memory_relations`

Relations между карточками.

Для MVP этого достаточно. Отдельные таблицы под каждый тип памяти не нужны.

---

## 38. Data growth model

Цель — чтобы число LONG cards росло существенно медленнее сообщений.

Пример:

```text
за месяц в группе:
3000 сообщений
↓
~300 кандидатов на memory update
↓
~80 meaningful cards/updates
↓
после compaction ~30–60 active LONG cards
```

Точные цифры определятся на реальном тесте.

Главный KPI — не абсолютный размер БД, а compression ratio и качество retrieval.

---

## 39. Memory Quality Metrics

Обязательные метрики для тестов:

### `false_memory_rate`

Сколько уверенных воспоминаний оказались неверными.

Цель MVP:

```text
< 2% для explicit factual callbacks
```

### `duplicate_active_rate`

Доля active cards, которые описывают фактически одно и то же.

Цель:

```text
< 10%
```

### `useful_callback_rate`

Сколько memory-based callbacks получили позитивный/продолженный interaction.

### `stale_memory_rate`

Как часто retrieval вытаскивает уже неактуальную память.

### `memory_context_tokens`

Средний объём LONG memory в generation request.

Цель:

```text
обычно <= 1200 tokens
```

### `compression_ratio`

Соотношение raw message volume к активному LONG knowledge.

---

## 40. MVP Acceptance Tests

Перед Friends Test система должна пройти минимум следующие сценарии.

### A. Explicit remember

```text
«НеНой, запомни: баня в субботу в 18:00»
```

Ожидание:

- одна active card;
- high confidence;
- правильный group scope.

### B. Duplicate update

Один факт повторили 3 раза.

Ожидание:

- не три карточки;
- одна обновлённая;
- evidence_count растёт.

### C. Supersede

```text
«Встречаемся в 18:00»
позже
«Перенесли на 20:00»
```

Ожидание:

- старая версия superseded;
- новая active.

### D. Pattern

Три независимых эпизода одинакового поведения.

Ожидание:

- pattern только после достаточного evidence;
- формулировка наблюдаемая, без диагноза.

### E. Running joke

Повторяющаяся шутка получает реакции и callbacks.

Ожидание:

- создаётся running_joke;
- работает fatigue penalty.

### F. Personal/Group isolation

Личный факт пользователя существует в Personal.

Ожидание:

- Group Retrieval его не возвращает ни при каких обычных запросах.

### G. Forget

```text
«НеНой, забудь это»
```

Ожидание:

- карточка перестаёт участвовать в retrieval.

### H. Roast restriction

```text
«Эту тему про меня не используй для подъёба»
```

Ожидание:

- `usage_policy.roast = false`;
- другие допустимые uses сохраняются.

---

## 41. Что откладываем после MVP

Пока НЕ нужны:

- отдельная graph database;
- сложный knowledge graph UI;
- автоматический импорт всей Telegram history;
- cross-group memory;
- глобальный профиль человека, доступный всем группам;
- сложные embeddings pipelines;
- многоуровневая онтология;
- fine-tuning Memory Mapper.

Сначала проверяем простую Memory Map на реальной жизни.

---

## 42. Итоговая архитектурная формула

```text
RAW MESSAGES
     ↓
HOT CONTEXT
     ↓
WARM WINDOW
     ↓
MEMORY MAPPER
     ↓
CANDIDATES
     ↓
DEDUP / CONFIRM / MERGE / CONTRADICT
     ↓
LONG MEMORY CARDS
     ↓
RELATIONS
     ↓
RETRIEVAL
     ↓
3–8 RELEVANT CARDS
     ↓
CONTEXT BUILDER
     ↓
НЕНОЙ
```

Главное правило всей Memory System:

> **Не хранить больше текста. Хранить больше смысла.**

---

## 43. Статус спецификации

Зафиксировано:

- Personal / Group memory isolation;
- HOT / WARM / LONG;
- Memory Card schema;
- card types;
- evidence;
- confidence / importance / freshness;
- lifecycle statuses;
- merge / compaction;
- supersede / contradiction;
- running jokes;
- callback fatigue;
- usage policy;
- raw retention;
- Memory Mapper contract;
- Retrieval scoring;
- context budgets;
- DB mapping;
- quality metrics;
- MVP acceptance tests.

Следующий документ: `DISPATCHER_SPEC.md`.
