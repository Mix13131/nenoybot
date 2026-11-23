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
и 14 целевых engine tests.

## CI и развёрнутая версия

Исполняемый commit `7b20d3f588301f3b75305f3640f2bb85b239b101`, дерево
`453c9e14f93a40759a5f61e7973ed679f062cb98`; дерево совпало с локальным
commit `8d954ba3821e58396a0440478ae31249f682d928` до обновления этого отчёта.

[GitHub Actions, run 524](https://github.com/Mix13131/nenoybot/actions/runs/45218350644):
**SUCCESS, 1720 passed in 63.21s**. Развёртывание началось после завершения CI.

Railway web и worker закреплены на этом точном commit. Последующее изменение
только этого отчёта не меняет работающий код.

## Фактическое восстановление

Все времена в таблице — 23 ноября 2026, МСК. Рабочие ID участников, группы,
исходные сообщения и ответы модели не публикуются.

| Время | Проверка | Результат |
| --- | --- | --- |
| 19:38:32 | Web prepare и read-only preview | Миграция 0018; candidates 1, eligible 1, rearmed 0, blocked 0 |
| 19:39:22 | Новый worker | Telegram verified, ready, 77 источников; старый worker затем REMOVED |
| 19:40:20 | Redeploy существующего build | Повторил старый preview из snapshot; записи и AI-вызова не было |
| 19:41:05 | Новый deploy с актуальным pre-deploy command | status rearmed, dry_run false, rearmed 1; один исходный запрос возвращён в pending |
| 19:42:03 | Решение нового worker | direct, RESPOND, source_backed_decision |
| 19:42:45 | Read-only CLI stats | Одна completed direct-задача; **4 исходящих сообщения sent**, очереди pending/uncertain отсутствуют |
| 19:43 | Проверка конечной конфигурации | Временные recovery/stats команды убраны; pre-deploy снова только prepare |

Telegram принял четыре части ответа на исходный запрос; повторять сообщение
пользователю не потребовалось. Это проверка обработки и доставки, а не оценка
качества наставнического текста.

Операторская команда сама не делает AI-вызовов или отправок: её рабочая квитанция
содержала ai_calls_made 0 и telegram_messages_sent 0. Последующую обработку
выполнил новый worker. Статистика подтверждает один дополнительный assessment
и один generation, оба settled/valid_json; reserved и uncertain operations — 0.
Всего за день: два assessment, один generation и прежний operator_smoke.
Расчётный ledger расход — 129 997 microUSD, из них восстановление добавило
99 296 microUSD. Это расчёт по настроенным токеновым тарифам до cache-скидок,
без Railway, а не счёт провайдера. Observe и отсутствие cap сохранены.

Архив по итогам: **77 доступных исходных текстов** — прежние 76 импортированных
и новое обращение. Повторного импорта не было.

### Конечные deployments

- Arni web: `87d857df-2266-4a17-a289-62285aea67d9`, SUCCESS,
  `/ready` — HTTP 200. Recovery apply выполнен предшествующим
  `90f77121-7eb2-4a1d-8031-ceabbad6d62b`; в конечном deployment его команды нет.
- Arni worker: `e38395f8-0c5d-4cb8-bf5f-e01deb19f5a6`, SUCCESS.
- Прежний worker `6e081700-5730-46a4-baf1-b6526111bd53` был REMOVED до apply.
- NeNoi web `ef3b1e63-acad-4a71-9ced-ccf74653543b`, NeNoi worker
  `88b3a70c-2526-4a6e-a934-672b85c5206f` и Postgres
  `c0613672-9d3d-4b3b-a969-09130a5ec874` сохранили прежние deployments.

Railway принимает одну строку pre-deploy command; зависимые команды объединены
через `&&`. Redeploy с deployment ID повторил настройки snapshot, поэтому
актуальная команда apply применена через connect-service-source с тем же точным
проверенным commit. Повторный preview ничего не изменил. Финальный stats запуск
был read-only, его временная настройка также удалена без дополнительного рестарта.

PR остаётся Draft и не слит. Разбор всех участников одной командой остаётся
отложенным по решению владельца; в это исправление он не входит.
