# Арни: все подключённые группы и темы — проверка 23 ноября 2026

## Решение владельца и границы

Владелец предоставил ссылки на темы одной группы и уточнил: бот должен работать
во всех группах, куда его добавляют, читать все темы и обходиться без команд.
Имя Арни уже выбрано. Работа продолжает PR #171, исходный HEAD
`70fb4e5d0d06e1cf9a2476ec911ac85661d56361`, принятый Stage A #169 сохранён.

Новый режим `COACH_GROUP_MODE=joined_groups` не использует фиксированный список
групп/тем. `COACH_CHAT_ID` может служить только первоначальной проверке доступа.
Прежний режим `configured` остаётся совместимым. Секреты, реальные chat ID,
переписка участников и рабочие данные в репозиторий не включены.

## Реализация

- Группы, General, обычные беседы и новые форумные темы обнаруживаются в
  аутентифицированном webhook. Первый источник, политика группы и задание
  сохраняются в одной транзакции до ACK.
- Общая архивная проекция расширяется только явным `allow_group_messages=True`;
  прежний Stage A по умолчанию остаётся строгим. Корень имеет канонический ID 1
  внутри своего chat ID; исходный Telegram payload не подменяется.
- `my_chat_member` хранит членство, время, update ID и период подключения.
  Удаление отменяет всю очередь группы. Повторное добавление отменяет старую
  работу по её исходному Telegram порядку; более новые сообщения, пришедшие
  раньше запоздавшего join, сохраняются.
- Известные редактирования исправляют архив даже после remove/rejoin и при
  запоздавшей доставке. Точная личность источника, текущий отказ автора и порядок
  редакций по-прежнему проверяются архивным repository.
- Блокировки перед отправкой: membership → scope → author → output. Удаление
  ждёт уже начатую HTTP-отправку и не позволяет начать следующие части.
- Worker обнаруживает scopes без рестарта. Ошибка/удаление одной группы не
  останавливает остальные. Очистка временных данных работает и без групп.
- Архив, AI-контекст, ответы и производные записи изолированы по группе/теме/
  автору; бюджет один на бота и резервируется до каждого AI-вызова.
- Настройка Telegram в новом режиме использует `setMySettings` с двумя полями:
  `can_join_groups=true`, `can_read_all_group_messages=true`; затем проверяет
  результат и идентичность через `getMe`. Admin-role не обязательна.

## Проверки и команды

Все сообщения, пользователи, тарифы и ответы провайдеров синтетические.
PostgreSQL 16.2 запускается отдельным временным кластером только на loopback.
Реальных Telegram/AI-запросов и миграций рабочей БД не было.

Рабочий каталог: `/workspace/scratch/3d2f1bc5ec88/nenoybot-arni`.
Python: `/workspace/scratch/3d2f1bc5ec88/.venv/bin/python`.
Для краткости ниже `PY` означает этот точный Python, `PG` —
`/workspace/scratch/3d2f1bc5ec88/run_local_pg.py`; это обозначения журнала,
а не новые требования к окружению.

| Команда | Результат |
|---|---|
| `bash scripts/codex_preflight.sh` | В стандартном PATH не найден pytest; это предупреждение не скрыто. |
| `env PATH=/workspace/scratch/3d2f1bc5ec88/.venv/bin:/usr/local/bin:/usr/bin:/bin bash scripts/codex_preflight.sh` | PASS, Python 3.12.14, pytest 9.2.0, правильная ветка, изначально чистое дерево. |
| `PY -m pytest tests_v2/unit/test_coach_config.py -q` | 36 passed. |
| `PY -m pytest tests_v2/unit/test_coach_config.py tests_v2/unit/test_coach_multigroup_runtime.py -q` | 46 passed после проверки глобальной очистки без групп. |
| `PY -m pytest tests_v2/unit/test_telegram_archive.py tests_v2/unit/test_coach_group_projection.py tests_v2/unit/test_coach_transport.py -q` | 192 passed после строгой проверки форумного подтверждения. |
| `PY -m pytest tests_v2/unit/test_coach_config.py tests_v2/unit/test_coach_multigroup_runtime.py tests_v2/unit/test_coach_group_projection.py tests_v2/unit/test_coach_transport.py -q` | 155 passed в промежуточной объединённой проверке. |
| `PY PG PY -m pytest tests_v2/integration/test_coach_lifecycle_postgres.py tests_v2/integration/test_coach_natural_dialogue_postgres.py tests_v2/integration/test_coach_external_review_postgres.py -q` | 40 passed, 15.83 s. |
| `PY PG PY -m pytest tests_v2/integration/test_coach_multigroup_postgres.py -q` | 15 passed, 7.25 s. |
| `PY PG PY -m pytest tests_v2/integration/test_coach_membership_races_postgres.py -q` | Сначала 11 passed; после регрессий 15 passed, 4.96 s. |
| `PY PG PY -m pytest tests_v2/integration/test_coach_membership_races_postgres.py -q -k edit_of_known_source` | 2 passed, 14 deselected, 1.21 s: после добавления варианта запоздавшей правки файл содержит 16 сценариев. |
| `PY -c 'import app_v2; import app_v2.coach_main; import app_v2.coach_worker; import app_v2.coach_admin'` | PASS. |
| `PY -m py_compile app_v2/coach_config.py app_v2/coach_admin.py app_v2/coach_main.py app_v2/coach_worker.py tests_v2/unit/test_coach_multigroup_runtime.py` | PASS. |
| `git diff --check` | PASS. |
| Первый `PY PG PY -m pytest tests_v2 -q` | 3 failed, 1330 passed, 50.78 s; причины и исправления ниже. |
| Итоговый `PY PG PY -m pytest tests_v2 -q` | **1334 passed, 0 failed, 0 skipped, 46.56 s**. Включает все 16 membership-race сценариев. |
| `git diff --name-only v2...HEAD` | Scope check: изменения только v2; legacy `app/` и `tests/` не затронуты. |

Промежуточный полный прогон обнаружил несовпадение класса ошибки идентичности
после выделения registry и два ожидаемых списка версий миграций, заканчивавшихся
на 0014. Общий repository exception сохранён через совместимый импорт;
оба списка включают 0015. Эти проверки не удалены и не ослаблены.

## Независимое ревью

Read-only reviewer проверил код и отдельно воспроизвёл отказы на свежих локальных
PostgreSQL-кластерах. Все выявленные замечания исправлены и повторно проверены:

1. Rejoin, пришедший раньше leave, не оживляет старый ответ.
2. Удаление между claim и обработкой задания не завершает весь worker.
3. Правка сообщения из той же секунды, что join, обновляет источник и отменяет
   подготовленный ответ по старой редакции.
4. Запоздавший первоначальный join сохраняет доказуемо новые задания и ответы.
5. Известный источник из предыдущего периода членства корректируется после
   возвращения бота; неизвестный старый оригинал не импортируется.
6. Правка, сделанная до leave, но доставленная после rejoin, также обновляет
   источник и не создаёт нового AI-задания.

Итог независимого ревью: открытых P1/P2 в проверенной части не осталось.
Это не внешнее GitHub Codex review нового commit и не оценка реальной модели.

## Railway и практическое подключение

В заранее созданных пустых сервисах Арни подготовлены ожидающие применения
переменные joined_groups и первоначальной проверки группы; worker получает
ссылку на защищённый Telegram-токен web-сервиса. Пользователь уже добавил сам
токен. Эти настройки не запускают сервисы: source и deployments отсутствуют.
Существующие сервисы НеНоя не менялись. Нет записи реальных сообщений в БД,
настройки живого webhook, вызова `setMySettings` или платного AI.

Для запуска ещё нужны выбранный владельцем дневной денежный предел, отдельная
логическая БД, точная модель/тарифы и HTTPS origin. После настройки обязательны
реальное обычное сообщение, General, отдельная тема и вторая группа. Возможность
Telegram читать историю старых чатов здесь не заявлена. Текущая граница
`real_model_quality_measured=false` сохраняется.

## Первичные источники

- https://core.telegram.org/bots/features#privacy-mode
- https://core.telegram.org/api/forum#interacting-within-topics
- https://core.telegram.org/bots/api#setmysettings
- https://core.telegram.org/bots/api#message
