# TASK 01 — `app_v2` Skeleton

## Статус

**READY FOR CODEX**

## Цель

Создать минимальный отдельный Python runtime НеНой 2.0 в namespace `app_v2/`, который импортируется и запускается независимо от legacy `app/`.

Это фундамент v2. На этом шаге НЕ реализуем Telegram, PostgreSQL, Dispatcher, Memory, LLM или бизнес-логику НеНоя.

Результат задачи — чистый, тестируемый skeleton, на который будут последовательно наращиваться следующие этапы.

---

## Контекст проекта

Перед началом прочитать:

- `docs/v2/PRODUCT_VISION.md`
- `docs/v2/PERSONALITY_SPEC.md`
- `docs/v2/MEMORY_SPEC.md`
- `docs/v2/DISPATCHER_SPEC.md`
- `docs/v2/ARCHITECTURE.md`
- `docs/v2/MVP_BUILD_PLAN.md`
- `docs/v2/DECISIONS.md`

Ключевое архитектурное решение:

- `app/` — legacy/reference v1, не трогать;
- `app_v2/` — новый runtime;
- `tests/` — legacy tests, не трогать;
- `tests_v2/` — новые tests.

Base branch: `v2`.
Рабочая branch для задачи: `task/v2-01-app-v2-skeleton`.

---

## Scope

### Разрешено изменять

- `app_v2/**`
- `tests_v2/**`
- `requirements.txt`
- `.env.example` — только добавление v2-переменных; существующие v1-переменные не удалять и не переименовывать

### Запрещено изменять

- `app/**`
- `tests/**`
- prompts/assets v1
- Railway/deployment v1
- `docs/v2/*.md`, кроме случаев, когда обнаружена фактическая ошибка в этой задаче — тогда не исправлять самовольно, а указать в отчёте
- product rules / Personality / Memory / Dispatcher policy

### Запрещено добавлять без необходимости

- ORM
- Redis
- Celery
- Kafka/RabbitMQ
- Alembic
- Docker/Kubernetes
- frontend/admin UI
- Telegram SDK
- OpenAI integration

---

## Что создать

Минимальная структура:

```text
app_v2/
├── __init__.py
├── main.py
├── config.py
├── domain/
│   └── __init__.py
├── adapters/
│   └── __init__.py
├── repositories/
│   └── __init__.py
├── services/
│   └── __init__.py
├── workers/
│   └── __init__.py
├── prompts/
│   └── .gitkeep
└── db/
    └── __init__.py

tests_v2/
├── __init__.py
├── unit/
│   └── __init__.py
├── integration/
│   ├── __init__.py
│   └── test_health.py
├── contracts/
│   └── __init__.py
└── scenarios/
    └── __init__.py
```

Допустима близкая структура, если она не усложняет architecture и сохраняет будущие namespace из `ARCHITECTURE.md`.

---

## Functional requirements

### 1. FastAPI entry point

В `app_v2/main.py` должен существовать importable FastAPI application:

```python
from app_v2.main import app
```

Приложение не должно запускать worker, scheduler, Telegram или LLM при import.

### 2. `/health`

Endpoint:

```text
GET /health
```

Должен отвечать HTTP `200` без доступа к внешним сервисам.

Рекомендуемый payload:

```json
{
  "status": "ok",
  "service": "nenoy-v2-web"
}
```

Точная дополнительная metadata допустима, но endpoint должен оставаться простым и детерминированным.

### 3. `/ready`

Endpoint:

```text
GET /ready
```

На TASK 01 readiness не проверяет PostgreSQL, потому что DB появится в TASK 03.

Пока endpoint может подтверждать, что конфигурация приложения успешно загружена.

HTTP `200` для валидной test/dev config.

Не проектировать сложную readiness framework заранее.

### 4. Configuration

Создать `app_v2/config.py`.

Требования:

- config читается из environment variables;
- должен быть явный environment (`development`, `test`, `production` или эквивалент);
- production mode должен уметь валидировать обязательный secret;
- отсутствие обязательного production secret должно приводить к понятной configuration error с конкретным сообщением;
- import `app_v2` сам по себе не должен требовать production secrets;
- test/dev режим должен позволять запуск tests без настоящих credentials.

Не добавлять реальные secrets в repository.

Минимальные v2 env names можно определить так:

```text
NENOY_V2_ENV
NENOY_V2_APP_NAME
NENOY_V2_WEBHOOK_SECRET
```

`NENOY_V2_WEBHOOK_SECRET` пока не используется Telegram handler, но production config должен уметь проверять его наличие как будущий required secret.

Если выбран другой naming — объяснить в отчёте причину.

### 5. Независимость от v1

Код внутри `app_v2/` не должен импортировать ничего из `app/`.

Это acceptance requirement.

---

## Dependencies

Добавить только зависимости, реально необходимые TASK 01.

Ожидаемо:

- `fastapi`
- `uvicorn`
- библиотека/подход для settings только если действительно оправдан
- dependency для HTTP test client, если текущего окружения недостаточно

Перед добавлением нового пакета сначала проверить, можно ли сделать задачу стандартной библиотекой + уже установленными dependency.

Не удалять существующие dependencies v1 из `requirements.txt`.

---

## Tests

Обязательные автоматические проверки:

### A. Import smoke test

```bash
python -c "import app_v2"
```

Ожидается exit code `0`.

### B. App import

```bash
python -c "from app_v2.main import app; print(app.title)"
```

Ожидается exit code `0`.

### C. Pytest

```bash
pytest tests_v2 -q
```

Минимум тесты должны проверять:

- `/health` → 200;
- `/health` содержит ожидаемый status;
- `/ready` → 200 в test config;
- production config без required secret завершается понятной configuration error;
- config test/dev создаётся без реальных secrets.

### D. Legacy protection

Проверить git diff и подтвердить, что `app/` и `tests/` не изменены.

Рекомендуемая команда:

```bash
git diff --name-only v2...HEAD
```

Если рабочая среда Codex использует другой base/ref, показать эквивалентную реально выполненную проверку.

---

## Acceptance Criteria

TASK 01 считается завершённой, только если выполнены все пункты:

- [ ] существует отдельный `app_v2/` namespace;
- [ ] существует отдельный `tests_v2/` namespace;
- [ ] `from app_v2.main import app` работает;
- [ ] FastAPI `/health` возвращает `200`;
- [ ] `/ready` существует и возвращает `200` в test/dev mode;
- [ ] configuration читается из env;
- [ ] production config без обязательного secret даёт понятную configuration error;
- [ ] import `app_v2` не зависит от legacy `app`;
- [ ] `pytest tests_v2 -q` реально запущен и проходит;
- [ ] import smoke commands реально запущены;
- [ ] legacy `app/` не изменён;
- [ ] legacy `tests/` не изменён;
- [ ] никаких Telegram/DB/LLM функций не добавлено вне scope.

---

## Что НЕ делать в TASK 01

Не начинать заранее TASK 02+.

В частности НЕ реализовывать:

- `EventEnvelope` и остальные domain contracts;
- PostgreSQL connection;
- migrations;
- webhook `/telegram/...`;
- Event Queue;
- Worker loop;
- OpenAI/model router;
- Dispatcher;
- Memory;
- Personality Engine;
- Telegram Sender;
- reminders;
- Railway deploy.

Пустые package namespaces допустимы. Их бизнес-логику оставляем будущим задачам.

---

## Failure / rollback notes

Если выбранная dependency создаёт конфликт с v1 requirements:

1. не переписывать v1 код;
2. зафиксировать конфликт;
3. выбрать минимальный совместимый вариант либо остановиться и описать blocker.

Если environment Codex не позволяет запустить FastAPI tests из-за внешней проблемы, не утверждать, что tests прошли. Показать точную команду и ошибку.

---

# ОБЯЗАТЕЛЬНЫЙ ОТЧЁТ ПОСЛЕ ВЫПОЛНЕНИЯ

Ответ Codex после работы должен иметь ровно такую структуру:

## 1. Что сделано

Перечислить конкретные изменения и файлы.

## 2. Как проверено

Для каждой реально выполненной команды указать:

```text
КОМАНДА:
<точная команда>

РЕЗУЛЬТАТ:
<exit code / passed / failed / skipped>
```

Не писать, что тест запускался, если он не запускался.

## 3. Что проверить вручную

Только то, что действительно требует ручной проверки.

Если ничего — написать `Ничего`.

## 4. Что не сделано

Все пункты scope, которые остались незавершёнными.

Если всё сделано — написать `Ничего`.

## 5. Ошибки и риски

Не скрывать warnings, flaky behavior, dependency conflicts и технический долг.

Если нет — написать `Не обнаружены`.

## 6. Изменения вне задачи

Перечислить все изменённые файлы вне разрешённого scope и объяснить причину.

Если таких нет — написать `Нет`.

## 7. Следующий рекомендуемый шаг

Ровно один следующий шаг.

Ожидаемый при успешном TASK 01:

> `TASK 02 — Domain Contracts + Enums`

---

## Definition of Done

Не использовать общие формулировки типа «всё успешно настроено» без доказательств.

Не заявлять о тестах без запуска.

Не скрывать ошибки.

Не выполнять незапрошенный refactor.

После завершения остановиться. Не переходить к TASK 02 самостоятельно.
