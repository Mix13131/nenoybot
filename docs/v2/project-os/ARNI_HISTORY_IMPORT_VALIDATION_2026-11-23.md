# Проверка импорта истории — 23 ноября 2026

## Объём

Парсер Telegram Desktop, миграция 0017, отдельное происхождение импортированных
снимков, атомарный preview/apply, совместимость с живыми правками, CLI и закрытый
временный HTTPS endpoint. Рабочие сообщения и идентификаторы в git не включены.
Legacy `app/` и `tests/` не изменены.

## Реально выполненные проверки

`bash scripts/codex_preflight.sh`: чистая исходная ветка, необходимые файлы на месте.
Системный `pytest` отсутствует в PATH; далее использован существующий `.venv`.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_telegram_history.py tests_v2/unit/test_telegram_archive.py -q
```

146 passed, в том числе 64 новых проверки парсера.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python /workspace/scratch/3d2f1bc5ec88/run_local_pg.py /workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/integration/test_source_history_import_postgres.py -q
```

22 passed in 6.17s на отдельном локальном PostgreSQL 16.2. Первый запуск дал
21 passed и ошибку самого теста: отсутствовали обязательные `since/until` при
вызове coverage. Тест исправлен, проверка повторена. Production не использовался.

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python /workspace/scratch/3d2f1bc5ec88/run_local_pg.py /workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2 -q
```

Первый полный прогон: 1576 passed, 2 failed — два прежних точных списка миграций
заканчивались на 0016. В ожидания добавлена 0017; проверки старых запретов и
параллельного запуска сохранены. Повтор: **1578 passed in 61.23s**, без skipped.

Независимое ревью кода, CLI, readiness и тестов: нет открытых P1/P2. Дополнительно
независимо воспроизведены 101 unit test и отдельный PostgreSQL-сценарий:
preview rollback → импорт до известной даты вступления → live edit → дедуп →
новый snapshot → пагинация → forget → запрет неизвестного старого сообщения.

После полного прогона исправлена найденная P3-погрешность: счётчик ботов,
обнаруженных через Telegram, складывается с ранее пропущенными парсером.
Существующий unit-тест расширен смешанным случаем:

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_history_import_service.py tests_v2/unit/test_coach_history_endpoint.py -q
```

**75 passed in 0.66s**. Полный набор точного публикуемого HEAD дополнительно
проверяет обычный GitHub CI; его фактический результат записывается в PR.

`git diff --check`: PASS. `python -m app_v2.coach_admin --help`: новая команда
и явные параметры доступны, запуск помощи не открывает БД и не обращается к API.
Проверка изменённых файлов на реальные идентификаторы и фрагменты частного
экспорта: совпадений нет.

## Подтверждённые свойства

- Preview откатывает scopes, grants, imports, parents и revisions.
- Повторный экспорт не размножает снимки; конфликт откатывает весь batch.
- Живой update сохраняет свой настоящий ID и совместимость с очередью.
- Автор, тема и исходная дата неизменяемы; при равной дате live выше import.
- Forget, индивидуальный tombstone и выключенные scopes не отменяются импортом.
- Новая версия отменяет ответы со старыми доказательствами.
- Конкурентный импорт не восстанавливает данные после завершённого forget.
- Загрузка не создаёт задания на разбор или Telegram-отправки.

## Практическая граница

Первый рабочий preview остановился до записи в БД с `CoachTelegramError`.
Добавлены безопасные сведения о фазе, методе и HTTP-статусе без токена,
описания провайдера, содержимого сообщения и идентификаторов авторов.
Для ограниченного `getChatMember` добавлен read-only резервный поиск точного
автора в списке администраторов, только при HTTP 400/403. Неизвестная личность
по-прежнему блокирует импорт. Официальное основание: Telegram Bot API,
[getChatMember](https://core.telegram.org/bots/api#getchatmember) и
[getChatAdministrators](https://core.telegram.org/bots/api#getchatadministrators).

```bash
/workspace/scratch/3d2f1bc5ec88/.venv/bin/python -m pytest tests_v2/unit/test_coach_history_import_service.py tests_v2/unit/test_coach_history_endpoint.py tests_v2/unit/test_coach_transport.py -q
```

168 passed. Проверены exact match, бот/человек, отсутствующий автор, некорректный
ответ, caching и отсутствие fallback для timeout/429/5xx.

Полный GitHub CI исправленного commit
`3e6571d477ff6dc23677ff40941fa700ba52d4c1` завершился **success**:
[run 45206787673](https://github.com/Mix13131/nenoybot/actions/runs/45206787673),
job `137804558399`, **1602 passed in 61.88s**. Это проверка точного исходного
кода исправления; успешный рабочий импорт из неё не следует.

### Повторный рабочий preview

23 ноября 2026, **18:39:20 МСК (15:39:20 UTC)**, повторный preview на этом commit
завершился **HTTP 503**. После развёртывания подтверждены **SUCCESS** у обоих
сервисов: `arni-web` и `arni-worker`. Worker завершил развёртывание в
18:39:23 МСК (15:39:23 UTC), на три секунды позже preview.

Подтверждённая безопасная диагностика:

```text
phase=author_identity
verified_authors=1
total_authors=2
reason=history_author_not_in_verified_administrators
method=getChatAdministrators
```

Личность одного из двух авторов подтверждена. Для второго автора резервный
поиск в списке администраторов не дал точного подтверждения личности. Импорт
остановлен до обращения к базе данных; **apply не вызывался, история не импортирована**.

Официальная документация Telegram гарантирует работу `getChatMember` для
других пользователей только боту, являющемуся администратором чата. Это общее
ограничение API: фактический текущий статус бота из полученной диагностики
не установлен, поэтому отсутствие у него прав администратора не заявляется.

Четыре временные настройки доступа к импорту очищены оператором. Развёртывание
web с отключённым доступом завершилось **SUCCESS**. Итоговая проверка:
`GET /ready` → **HTTP 200**, `POST /admin/history-import` с прежним секретом →
**HTTP 404**. Проверочный скрипт завершился с кодом **0**: временный endpoint
закрыт, готовность основного приложения сохранена.

Успешный импорт доказывает доступность истории в архиве и поиске. Качество
наставнических ответов и фактическую доставку новых сообщений подтверждает
последующее обычное использование. Отсутствующие в экспорте темы и версии
редактирования не объявляются восстановленными.
