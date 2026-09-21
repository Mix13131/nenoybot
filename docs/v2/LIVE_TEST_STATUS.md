# НеНой 2.0 — Live Test Status

Updated: 2026-09-21

## Current checkpoint

Рабочая линия — `v2`; `main`, legacy `app/` и legacy `tests/` без отдельного прямого решения не меняются.

Текущая production/code точка: `0fe8c260761c446aca9a3fdfd3ae1b00c6a8f05b` — merge PR #110 в `v2`.

### Что принято и доказано

| Scope | Итог | Live status |
| --- | --- | --- |
| TASK 29 / #92 | trusted memory/evidence, feedback, replay/idempotency, truthful operation receipts | deployed in current tree |
| TASK 30 / #94 | calendar/timezone reminders + cancellation | **LIVE PASS**: timezone clarification → persisted one-shot → real Telegram fire |
| #103 / #104 | natural one-shot order + Moscow clarification | **LIVE PASS** |
| #105 / #106 | production migrations + transaction rollback | **LIVE PASS**: `0003/0004` applied; worker stable |
| #107 / #108 | bounded 1-minute burst in finite window | implemented |
| #109 / #110 | Scheduled Action Interpreter v1 | **LIVE PASS** on novel verb `удивляй` |
| Live UX 2026-09-21 | response depth / progressive disclosure | **FINDING ACCEPTED**; docs fixed, runtime implementation pending |

Последняя подтверждённая полная CI-проверка финального PR #110 head — **494 passed, 1 warning** на isolated PostgreSQL 16.

Railway после merge PR #110:
- `nenoy-v2-web` — `SUCCESS`;
- `nenoy-v2-worker` — `SUCCESS`;
- worker startup: migrations ready / no pending migrations;
- Telegram webhook configured, `pending_update_count=0`, Telegram last error absent;
- `/ready` → 200;
- после запуска нет новых traceback / aborted-transaction failures.

### Controlled live acceptance уже пройден

1. Обычный Group direct path после deploy: Telegram → webhook 200 → worker → ответ — PASS.
2. `НеНой, напомни о себе в HH:MM сегодня` → запрос timezone → reply `Москва` → persisted reminder → реальный fire — PASS.
3. Production schema drift `pending_calendar_intents` был найден живым тестом и закрыт migrations/rollback hardening.
4. Semantic scheduled action `в течение следующих пяти минут каждую минуту удивляй меня` → persisted bounded series → разные generated actions на последовательных fires — PASS.
5. Truthful receipt enforcement расширен на любые semantic future-action promises: без реального persisted schedule модель не может заявить, что будет выполнять действие.

### Live UX finding — response depth

Реальный тест диалога 2026-09-21 выявил отдельный conversational UX-дефект: обычный экспертный вопрос получил многоэкранный ответ, а фраза пользователя `поясни детальнее, не понятно` привела к ещё большему расширению темы вместо более простого объяснения.

Принятое направление исправления:

- default — минимально достаточный ответ, а не исчерпывающая статья;
- `не понял / поясни / объясни` → сделать текущий тезис яснее, не шире;
- `подробнее` → раскрыть один следующий слой текущего тезиса;
- глубокий режим включать по явному запросу на исследование/детальный разбор;
- не решать проблему жёстким глобальным лимитом символов;
- критичные safety / medical / legal / compliance оговорки сохранять, но вывод ставить первым.

Нормативно закреплено в [PERSONALITY_SPEC.md](PERSONALITY_SPEC.md), §2.1 и [DECISIONS.md](DECISIONS.md), D-052.

**Статус:** решение принято и документация обновлена; изменение runtime/prompt policy ещё не реализовано и не проверено live.

### TASK 27 / #76 — Friends Test Preflight: PASS

Подтверждённая preflight evidence:
- 2026-09-14 была явно выбрана и активирована одна контролируемая тестовая группа;
- friends profile стартовал с low initiative (`initiative=3`);
- ordinary non-mention group message достиг v2 и остался без unsolicited ответа;
- direct mention достиг v2 и получил ответ;
- факт доставки ordinary group traffic доказывает, что Telegram observation path работает; отдельное повторное чтение BotFather setting не требуется для функционального gate;
- post-token-rotation recovery подтверждён merged PR #84;
- текущий полный CI сохраняет privacy regression: Group retrieval/context не fallback-ится в Personal scope;
- serious/sensitive gates подавляют roast;
- mute/silence path и reaction/feedback semantics покрыты тестами;
- group analytics включает organic participants, direct mentions, unsolicited interventions, reactions, negative feedback и mute events.

Preflight закрывает готовность **одной controlled group**. Он не является завершённым Friends Test и не разрешает массовый whitelist/rollout.

Следующая граница: **7-дневный Controlled Friends Test**, затем Product Review v0.2.

## Historical reminder-controls checkpoint verified on 2026-09-15

Через GitHub проверены статусы `merged=true`, целевая ветка `v2` и merge SHA всех трёх PR; также просмотрен patch PR #90.

| PR | Изменение | Merge commit |
| --- | --- | --- |
| [#88](https://github.com/Mix13131/nenoybot/pull/88) | Первоначальная ручная отмена групповых напоминаний | `baa414b6d13a9f636dde27955b9d605121fa015d` |
| [#89](https://github.com/Mix13131/nenoybot/pull/89) | Отмена цепочки отделена от mute; распознаётся естественная фраза «Горшочек, не вари…», предусмотрен ответ о результате | `77d287f634c2f6004478594d09ab48b67e2346dd` |
| [#90](https://github.com/Mix13131/nenoybot/pull/90) | Reply-stop, отмена по адресату/группе независимо от автора, подавление ожидающих событий/outbox и ограничения повторений | `315304148fac4014e98d9e39b31ebfcb93984763` |

**Правило из PR #88 «только автор может отменить» заменено PR #90.** Для текущих групповых stop-команд не восстанавливать это ограничение: любой участник той же группы может остановить напоминания в пределах этой группы.

Основные файлы исторического исправления PR #90:

- `app_v2/services/group_reminders.py`;
- `app_v2/repositories/reminder_repo.py`;
- регрессионные тесты в `tests_v2/` (см. patch PR #90).

## Accepted reminder controls — no button

| Действие в чате | Ожидаемый scope отмены |
| --- | --- |
| Ответить прямо на сообщение-напоминание НеНоя: `стоп`, `хватит` или `достаточно` | Именно связанная с этим сообщением активная цепочка |
| `НеНой, хватит напоминать @username` | Активные напоминания этому адресату в текущей группе, независимо от автора |
| `НеНой, останови все напоминания` | Все активные напоминания текущей группы |
| Ответ адресата для цепочки с `stop_on_reply` | Автоматическая остановка соответствующих ожидающих ответа цепочек |

`@username` здесь — условный пример, не реальный участник production-чата.

Отмена напоминаний не должна переводить самого НеНоя в mute. Бот сообщает результат операции и остаётся доступным для обычного общения. Нулевое число отменённых записей не является успешной отменой.

Предохранители: для interval-reminders минимальный интервал — **15 минут**, лимит по умолчанию — **4 срабатывания**. Calendar recurrence (`каждый день`, будни, пятница) после TASK 30 работает **до явной отмены**, а не обрывается после четырёх запусков.

Отмена также подавляет связанные `events` и `outbox` в состояниях `pending`/`retry`. Это не доказательство отсутствия гонки с уже обрабатываемой или отправляемой записью: обещание «после отмены невозможен ни один пинг» не давать без отдельной проверки такого сценария.

**Решение пользователя от 2026-09-15: кнопку «🛑 Стоп» под напоминаниями не добавлять.** Это отклонённое предложение, не незавершённая задача и не отложенная функция. Не возвращать его в roadmap без нового прямого запроса пользователя. См. [DECISIONS.md](DECISIONS.md), D-046–D-051.

## Prior operational report — historical, not rechecked here

В предыдущей рабочей сессии было сообщено:

- одна активная 30-минутная цепочка была принудительно отменена через БД;
- после аварийной отмены проверка показала 0 активных напоминаний по тому тестовому адресату/поводу;
- нормальный worker был восстановлен после аварийной проверки;
- Railway web, worker и PostgreSQL имели статусы `SUCCESS`;
- на одном из предшествующих этапов исправления тесты дали `267 passed, 2 skipped`.

Источник этих операционных результатов — пересланные пользователем сообщения предыдущей сессии, а не повторный доступ к БД/Railway или новый запуск тестов. Число `267 passed, 2 skipped` не приписывать автоматически итоговому PR #90 или текущему HEAD.

Этот исторический блок не используется как доказательство текущего состояния. Актуальный production health-check 2026-09-20 описан в Current checkpoint выше: web/worker SUCCESS, migrations ready, webhook healthy, `/ready` 200. Идентификаторы чатов, реальные usernames, тексты production-переписки, токены и секреты не публикуются.

## Historical Personal smoke — 2026-09-14

Ранее зафиксировано: **functional PASS**.

Observed real path:
`Telegram -> webhook 200 -> PostgreSQL event -> worker -> OpenAI -> outbox -> Telegram sendMessage 200 -> user received reply`.

Тогда runtime проекта `nenoy-v2` (`nenoy-v2-web`, `nenoy-v2-worker`, PostgreSQL и Telegram webhook) был отмечен как live. Это историческая проверка, а не текущий health-check.

Первый запуск выявил credential-bearing transport logging и HTTP 400 strict JSON schema Memory Mapper. PR #73/#74 закрыли logging/schema issues; PR #84 был сделан именно для post-token-rotation webhook recovery. В текущем production-check webhook снова подтверждён healthy, поэтому этот старый blocker считается пройденным и не переносится в Friends Test.

## Continuation boundary

Следующий gate — **Controlled Friends Test**, а не новый hardening cycle.

На этом этапе:
1. Days 1–2: low initiative, observation + direct replies;
2. Days 3–4: medium initiative, cautious callbacks;
3. Days 5–7: higher roast/callback only if feedback stays healthy;
4. собирать product metrics и реальные диалоги;
5. чинить только воспроизводимые live blockers / truthful / privacy / safety regressions;
6. после 7 дней — Product Review v0.2 по фактическим данным.

Не подключать новые группы автоматически, не расширять scope функциональности без live-сигнала и не возвращаться к широкому parser-hardening без воспроизводимого пользовательского сбоя.


Память и действия разных групп остаются изолированы. Полный Friends Test, состав whitelist и rollout во вторые/новые группы этой записью не закрываются.
