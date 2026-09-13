# CODEX SETUP — НеНой 2.0

Цель: один раз настроить Codex так, чтобы последующие задачи v2 запускались с корректным репозиторием, зависимостями, инструкциями и проверками.

## 1. Repository

Codex должен иметь доступ к:

```text
Mix13131/nenoybot
```

Основная линия v2:

```text
v2
```

Текущая рабочая ветка первой задачи:

```text
task/v2-01-app-v2-skeleton
```

Не использовать `main` для разработки v2.

## 2. Создать Codex Cloud environment

В Codex открыть Settings → Environments и создать environment для `Mix13131/nenoybot`.

Codex Cloud при старте создаёт изолированный container и checkout выбранной ветки/commit. Репозиторий содержит корневой `AGENTS.md`; Codex должен автоматически прочитать его как persistent project instructions.

## 3. Setup script

В поле Setup script указать:

```bash
bash scripts/codex_setup.sh
```

Скрипт:

- показывает Python/pip versions;
- устанавливает `requirements.txt`;
- ставит bootstrap-пакеты FastAPI/uvicorn/httpx, необходимые для первого v2 task.

Важно: bootstrap dependencies не заменяют `requirements.txt`. TASK 01 всё равно обязан записать реальные runtime dependencies в repository metadata.

## 4. Maintenance script

Если Codex Environment предлагает Maintenance script, использовать:

```bash
python -m pip install -r requirements.txt
```

Это обновит dependencies при повторном использовании cached environment после изменения branch.

## 5. Internet access

Для текущего TASK 01 интернет агенту после setup не нужен.

Рекомендуемое состояние:

```text
Agent internet access: OFF
```

Setup phase сама имеет доступ для установки Python packages. Если будущая задача действительно требует network access, включать его отдельно и минимально, а не оставлять unrestricted по умолчанию.

## 6. Environment variables / secrets

Для TASK 01 настоящие production credentials не нужны.

Не добавлять в Codex environment реальные:

- Telegram bot token;
- OpenAI API key;
- production database URL;
- webhook secret.

TASK 01 обязан работать в development/test mode без настоящих credentials.

Production secrets появятся позже в Railway runtime, а не в исходном коде.

## 7. Первый запуск

Выбрать branch:

```text
task/v2-01-app-v2-skeleton
```

Перед изменениями Codex должен выполнить:

```bash
bash scripts/codex_preflight.sh
```

После этого дать задачу:

```text
Выполни docs/v2/tasks/TASK_01_APP_V2_SKELETON.md строго по спецификации.
Сначала прочитай AGENTS.md и выполни preflight.
Не выходи за scope TASK 01 и не переходи к TASK 02.
Все обязательные проверки реально запусти.
После реализации запусти bash scripts/codex_validate_task01.sh.
В конце дай отчёт ровно в формате TASK 01.
```

## 8. Что должен сделать Codex самостоятельно

Внутри task environment Codex может:

- читать repository;
- редактировать разрешённые task files;
- запускать terminal commands;
- запускать pytest;
- показать diff;
- после завершения предложить Create PR.

Не просить Codex merge-ить task branch автоматически. Сначала review diff и отчёта, затем merge в `v2`.

## 9. Review gate

TASK 01 не считается выполненным только по сообщению агента.

Перед merge проверяем:

```text
- полный Codex report;
- git diff;
- реально запущенные tests;
- отсутствие изменений app/ и tests/;
- acceptance criteria TASK 01.
```

Только после review начинается TASK 02.

## 10. Если Codex environment не видит repository

Проверить доступ GitHub/Codex к `Mix13131/nenoybot` в Codex repository settings. Подключение GitHub к обычному ChatGPT и доступ Codex Cloud могут отображаться в разных product surfaces, поэтому repository должен быть явно доступен при создании Codex environment.

## 11. Если dependency cache устарел

В Environment settings выполнить Reset cache либо перезапустить setup. Codex cloud cache может переживать несколько chats, поэтому после существенного изменения dependencies это нормальная операция.

## 12. Persistent instructions

Root `AGENTS.md` — постоянная инструкция Codex для всех v2 задач. Task-specific правила всегда берутся из `docs/v2/tasks/TASK_*.md` и имеют более узкий scope.

Не превращать AGENTS.md в длинный product spec: подробное продуктовое знание остаётся в `docs/v2/` и подгружается только когда этого требует задача.
