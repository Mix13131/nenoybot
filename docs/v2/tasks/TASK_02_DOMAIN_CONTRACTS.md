# TASK 02 — Domain Contracts + Enums

## Статус

**READY TO IMPLEMENT**

## Цель

Зафиксировать typed domain contracts НеНой 2.0, на которых дальше будут строиться Dispatcher, Memory, Personality, Actions, workers и adapters.

На этом шаге НЕ реализуем Telegram, PostgreSQL, LLM, Dispatcher policy или бизнес-логику. Наша цель — только чистые, сериализуемые и валидируемые domain objects.

---

## Контекст

Перед началом прочитать:

- `AGENTS.md`
- `docs/v2/PERSONALITY_SPEC.md`
- `docs/v2/MEMORY_SPEC.md`
- `docs/v2/DISPATCHER_SPEC.md`
- `docs/v2/ARCHITECTURE.md`
- `docs/v2/MVP_BUILD_PLAN.md`

Base branch: `v2`.
Рабочая branch: `task/v2-02-domain-contracts`.

---

## Разрешено менять

- `app_v2/domain/**`
- `tests_v2/contracts/**`
- `requirements.txt` только если контрактная библиотека действительно отсутствует и нужна

## Запрещено менять

- `app/**`
- `tests/**`
- runtime/deployment v1
- `app_v2/main.py`
- `app_v2/config.py`
- adapters/repositories/services/workers
- product rules/specs

---

## Технологическое решение

Использовать Pydantic v2, который уже является зависимостью FastAPI. Не добавлять dataclasses + ручную валидацию параллельно и не добавлять отдельную schema library.

Все contracts должны быть Telegram-agnostic: никаких `Update`, `Message` или объектов конкретного SDK.

---

## Файлы

Минимально создать:

```text
app_v2/domain/
├── __init__.py
├── enums.py
├── events.py
├── decisions.py
├── memory.py
├── personality.py
├── actions.py
├── outbound.py
├── feedback.py
└── usage.py

tests_v2/contracts/
├── test_events.py
├── test_decisions.py
├── test_memory.py
├── test_personality.py
└── test_misc_contracts.py
```

---

## Enums MVP

Нужно минимум:

### ScopeType
- `personal`
- `group`

### PrimaryAction
- `ignore`
- `reply`
- `act`
- `schedule`

### SecondaryAction
- `remember`
- `update_memory`
- `link_memory`
- `record_feedback`
- `create_task`
- `create_reminder`
- `reschedule`
- `cancel_reminder`

### EventType
Минимум event types из `DISPATCHER_SPEC.md`:
- private/group message events;
- feedback events;
- reminder/commitment/followup/support events;
- memory lifecycle events.

### ResponseMode
Personal:
- `assistant`
- `coach`
- `mirror`
- `care`
- `observer`
- `execution`

Group:
- `group_direct_reply`
- `group_banter`
- `group_roast`
- `group_callback`
- `group_help`
- `group_organizer`
- `group_arbiter`

### MemoryStatus
- `candidate`
- `active`
- `archived`
- `superseded`
- `rejected`

### MemoryOrigin
Минимум:
- `explicit`
- `inferred`
- `system`

### ReasonCode
Добавить стабильный стартовый набор reason codes, необходимый будущему Dispatcher. Не пытаться перечислить все будущие причины. Минимум direct mention, reply-to-bot, callback, contradiction, roast opportunity, help opportunity, cooldown, silence request, serious context, sensitive context.

---

## Contracts

### EventEnvelope

Поля:
- `event_id: str`
- `event_type: EventType`
- `occurred_at: datetime`
- `scope_type: ScopeType`
- `scope_id: str`
- `actor_user_id: str | None`
- `message_id: str | None`
- `reply_to_message_id: str | None`
- `text: str | None`
- `metadata: dict[str, Any]`

Timezone-aware datetime обязателен.

### SceneAnalysis

Поля:
- `direct_mention: bool`
- `reply_to_bot: bool`
- `question_to_bot: bool`
- `command_intent: str | None`
- scores `0..1`: banter, seriousness, conflict, sensitivity, roast opportunity, callback opportunity, help opportunity, memory value, contradiction, commitment, decision.

Score за пределами `0..1` должен отклоняться.

### DispatcherDecision

Поля минимум:
- `primary_action: PrimaryAction`
- `secondary_actions: list[SecondaryAction]`
- `mode: ResponseMode | None`
- `intervention_score: int | None` (`0..100`)
- `reason_codes: list[ReasonCode]`
- `target_user_id: str | None`
- `selected_memory_ids: list[str]`
- `metadata: dict[str, Any]`

Правило: `primary_action=reply` требует `mode`.
`primary_action=ignore` не должен требовать mode.

### MemoryEvidence

Поля:
- `message_id: str | None`
- `author_id: str | None`
- `timestamp: datetime`
- `excerpt: str`

### UsagePolicy

Поля:
- `assist: bool = True`
- `callback: bool = True`
- `roast: bool = False`
- `proactive: bool = False`

### MemoryCard

Поля согласно `MEMORY_SPEC.md`:
- identity/scope/type/subject keys;
- summary/payload;
- importance/confidence/freshness `0..1`;
- lifecycle fields;
- evidence/source_count;
- usage_policy.

MVP contract может использовать `memory_type: str`, чтобы не преждевременно замораживать полный enum типов памяти.

Жёсткое правило scope:
- `scope_type=group` должен иметь непустой `scope_id`;
- `scope_type=personal` должен иметь непустой `scope_id`.

### MemoryRelation

Поля минимум:
- `id: str`
- `scope_type: ScopeType`
- `scope_id: str`
- `from_memory_id: str`
- `relation_type: str`
- `to_memory_id: str`
- `weight: float` (`0..1`)

### PersonalityState

Все числовые personality values — int `0..10`:
- directness
- brevity
- warmth
- pressure
- humor
- sarcasm
- roast
- profanity_level
- profanity_frequency
- initiative
- callback
- challenge
- care
- playfulness
- sensitivity

Плюс `mode: ResponseMode` и optional metadata.

### ActionRequest

Минимум:
- `action_type: str`
- `scope_type: ScopeType`
- `scope_id: str`
- `actor_user_id: str | None`
- `target_user_id: str | None`
- `payload: dict[str, Any]`
- `requested_at: datetime`

### OutboundMessage

Минимум:
- `message_id: str`
- `scope_type: ScopeType`
- `scope_id: str`
- `text: str`
- `reply_to_message_id: str | None`
- `dedupe_key: str`
- `metadata: dict[str, Any]`

### FeedbackEvent

Минимум:
- `feedback_id: str`
- `scope_type: ScopeType`
- `scope_id: str`
- `user_id: str | None`
- `intervention_id: str | None`
- `feedback_type: str`
- `value: float | str | bool | None`
- `occurred_at: datetime`
- `payload: dict[str, Any]`

### LLMUsageRecord

Минимум:
- `usage_id: str`
- `event_id: str | None`
- `intervention_id: str | None`
- `task_kind: str`
- `model: str`
- `input_tokens: int >= 0`
- `output_tokens: int >= 0`
- `cached_tokens: int | None >= 0`
- `estimated_cost_usd: float >= 0`
- `latency_ms: int >= 0`
- `success: bool`
- `created_at: datetime`

---

## Pydantic rules

- использовать `ConfigDict(extra="forbid")` для core contracts;
- enums сериализуются строками;
- datetime должны быть timezone-aware там, где событие реально привязано ко времени;
- mutable defaults создавать через `Field(default_factory=...)`;
- не выполнять IO при создании domain objects.

---

## Tests / Acceptance

Обязательно реально проверить:

```bash
python -c "from app_v2.domain.events import EventEnvelope; print(EventEnvelope.__name__)"
pytest tests_v2/contracts -q
pytest tests_v2 -q
```

Контрактные tests должны минимум доказать:

- valid `EventEnvelope` round-trip model_dump/model_validate;
- invalid scope/event enum отклоняется;
- naive `occurred_at` отклоняется;
- SceneAnalysis rejects score > 1 and < 0;
- `DispatcherDecision(reply)` без mode отклоняется;
- `DispatcherDecision(ignore)` без mode разрешён;
- MemoryCard rejects quality values outside `0..1`;
- group MemoryCard с пустым scope_id отклоняется;
- PersonalityState rejects values outside `0..10`;
- LLMUsageRecord rejects negative tokens/cost/latency;
- extra unknown fields отклоняются для core contracts;
- весь `tests_v2` остаётся зелёным.

---

## Что НЕ делать

Не реализовывать:
- repositories;
- DB schema/migrations;
- Dispatcher policy;
- Memory Mapper;
- Telegram adapter;
- LLM calls;
- Action execution;
- TASK 03+.

---

# ОБЯЗАТЕЛЬНЫЙ ОТЧЁТ

В конце дать:

1. Что сделано.
2. Как проверено — точные команды и фактический результат.
3. Что проверить вручную.
4. Что не сделано.
5. Ошибки и риски.
6. Изменения вне задачи.
7. Ровно один следующий шаг: `TASK 03 — PostgreSQL Schema + Migration Runner`.
