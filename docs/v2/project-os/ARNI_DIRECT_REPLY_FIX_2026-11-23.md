# Арни: прямой запрос завершился без ответа — 23 ноября 2026

## Наблюдение

В 19:15 МСК Telegram доставил прямое обращение: webhook ответил HTTP 200.
Последующая статистика показала 77 архивных текстов, одну завершённую задачу
`direct`, отсутствие исходящих ответов и один оплаченный этап `assessment`
с корректным JSON. Этап `generation` не запускался. Суммарный расход вырос
с 338 до 30 701 microUSD; финансового ограничения в режиме `observe` не было.

Старый worker не сохранял причину такого завершения. Поэтому точная причина
первого ответа модели не восстанавливается из этих агрегатов. Содержание
рабочих сообщений, имена, идентификаторы и ответы модели в репозиторий не включены.

## Воспроизведённый дефект и исправление

Собственное сообщение участника попадало в `conversation` целиком, а в
`sources` — только первыми 1200 символами. Точная цитата из конца видимого
контекста отклонялась как отсутствующая в источнике. Синтетическое воспроизведение
давало `fact_is_not_an_exact_source_quote` после одного этапа assessment.

Теперь собственная реплика, уже помещённая в явный бюджет контекста, имеет
тот же текст в evidence. Расширение происходит только после проверки бюджета,
единственного актуального proof, автора, темы, message ID и точного совпадения
текста с архивом. Уже известные источники также обновляются. Текст помощника
не превращается в доказательство о пользователе; checksum и повторная проверка
доступа сохранены.

Для явных direct/review/track запросов техническая ошибка проверки или
модельное молчание дают короткое уведомление без цитат и выводов. Отказы по
доступу, устаревшей задаче, чувствительному/внешнему источнику и неизвестные
причины не обходятся. Для обычных событий `auto_report` и `index` молчание
остаётся допустимым. Причина завершения и агрегат решения теперь сохраняются
без сообщений, идентификаторов или сырого ответа провайдера.

## Однократное восстановление исходного запроса

Миграция 0018 добавляет `recovery_attempt` (0 или 1) и время запроса восстановления.
CLI `recover-direct` по умолчанию выполняет read-only preview; запись требует
`--apply`, точной группы и временного окна не шире часа. Неоднозначный выбор
блокируется.

Восстанавливается существующая задача с исходными Telegram update/message ID,
job ID и сроком жизни. Текст берётся только из точной актуальной архивной ревизии.
Любой прежний исходящий ответ, в том числе cancelled или uncertain, изменение
прав/членства/источника, удаление, истечение срока или прежний этап generation
блокируют восстановление. Учёт прежней оплаченной операции сохраняется;
новая явно операторская попытка имеет отдельный namespace. Повторный apply
не запускает вторую попытку.

Порядок развёртывания обязателен:

1. Web: миграция и recovery preview.
2. Новый worker: SUCCESS; прежний deployment должен завершиться (REMOVED).
3. Отдельный recovery apply, затем проверка guarded delivery.
4. Удаление временной команды восстановления из pre-deploy.

Пример операторской команды без рабочих идентификаторов:

```bash
python -m app_v2.coach_admin recover-direct --chat-id "$COACH_CHAT_ID" --since "$RECOVERY_SINCE" --until "$RECOVERY_UNTIL"
```

## Проверки до развёртывания

`bash scripts/codex_preflight.sh`: исходная ветка чистая, legacy не затронут.
Системный `pytest` отсутствовал в PATH; проверки выполнялись существующим `.venv`.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_engine.py -q
```

85 passed, 4 skipped: четыре PostgreSQL-проверки требуют отдельной тестовой БД.
Их включил общий прогон ниже. Семь новых проверок закрывают цитату из
хвоста контекста, ранее известный источник, подмену user turn и превышение бюджета.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_recovery_cli.py tests_v2/unit/test_coach_history_import_service.py tests_v2/unit/test_coach_history_endpoint.py -q
```

107 passed in 0.82s. Проверены явный apply, точные границы, запрет лишних параметров
и отсутствие скрытого повторного вызова.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python /workspace/scratch/3d2f1bc5ec88/run_local_pg.py /workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_worker.py tests_v2/integration/test_coach_repo_postgres.py tests_v2/integration/test_coach_lifecycle_postgres.py -q
```

Первый этап: 123 passed in 9.34s. После поддержки recovery attempt:
127 passed in 10.96s. Проверены guards, безопасные notices, отсутствие публикации
после отзыва доступа, старые lease/attempt и отдельный namespace.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_worker.py tests_v2/unit/test_coach_operator_statistics.py tests_v2/unit/test_coach_multigroup_runtime.py -q
```

69 passed in 0.49s.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python /workspace/scratch/3d2f1bc5ec88/run_local_pg.py /workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_recovery.py tests_v2/integration/test_coach_recovery_postgres.py -q
```

36 passed in 8.62s. Проверены preview без записи, однократный apply, отдельный
ledger namespace, реальный worker с синтетическими provider/sender, один guarded
send и сохранность исходного ledger. Отрицательные сценарии включают все шесть
состояний output, control output без job ID, отзыв доступа, forget, правку,
истечение срока, lifecycle, неоднозначный выбор, другой bot scope и гонку apply.
Исправлены найденные тестами нормализация времени при другой timezone БД и
побочная запись при отсутствии membership.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python /workspace/scratch/3d2f1bc5ec88/run_local_pg.py /workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2 -q
```

**1720 passed in 68.24s**, свежий локальный PostgreSQL 16.2, без skipped.
`git diff --check` и CLI help — PASS. Рабочие сообщения, авторские ID и legacy
`app/`, `tests/` в изменение не попали.

Независимый review engine, prompts, worker/repo, migration, recovery, CLI и
readiness: **PASS, открытых P1/P2 нет**. Reviewer отдельно проверил 54 worker
и 14 целевых engine tests. CI и фактическое восстановление фиксируются по
завершении; успешная рабочая доставка пока не заявляется.
