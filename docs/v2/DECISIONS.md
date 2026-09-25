# DECISIONS — НеНой 2.0

Короткий журнал принятых продуктовых и архитектурных решений. Используется как источник истины, чтобы не переобсуждать уже зафиксированные вещи без причины.

## 2026-09-13

### D-001 — НеНой 2.0 развивается отдельно от v1

- `main` остаётся линией текущей v1.
- `v2` — отдельная линия разработки новой версии.
- Изменения v2 не должны ломать существующую v1.
- Merge `v2 → main` не является автоматической целью.

### D-002 — Для одновременной жизни v1 и v2 нужен отдельный runtime

Когда начнётся запуск v2, потребуется отдельный Telegram bot token, webhook и Railway service/deployment. Данные v1 и v2 должны быть разделены.

### D-003 — Один Core, два режима

- Personal.
- Group.

Это не два независимых продукта и не две кодовые базы. Общие движки переиспользуются, контексты и память разделены.

### D-004 — Personal Memory не попадает в Group Memory автоматически

Групповой движок не должен получать личную память участника только потому, что НеНой знает её из приватного диалога.

### D-005 — Память строится как карта, а не как бесконечная история

Сырые сообщения используются для HOT/WARM контекста. Долговременное знание хранится в компактных Memory Cards и связях между ними.

### D-006 — Характер задаётся параметрами

Личность НеНоя нельзя сводить к одному большому системному промпту. Используются Core Personality, Context Profile, Participant Adaptation и Situational Override.

### D-007 — У каждой группы свой уровень мата

Для группы отдельно задаются как минимум:

- `profanity_level` — максимальная допустимая жёсткость;
- `profanity_frequency` — частота использования мата.

Мат не обязан использоваться только потому, что разрешён высокий уровень.

### D-008 — Roast и profanity — разные параметры

Можно делать жёсткий подъёб без мата и матерную живую речь без сильного roast. Эти параметры не смешиваются.

### D-009 — Group Mode обязан уметь молчать

Самостоятельные вмешательства должны быть редкими и основанными на opportunity/context. `SILENCE` — полноценное решение Dispatcher.

### D-010 — Первый Group-полигон — реальный чат друзей

Тестируем не постановочные сценарии, а естественное общение. Основная метрика — начали ли другие участники сами обращаться к НеНою.

### D-011 — Сначала спецификации, потом код

Порядок:

`Vision → Personality → Memory → Dispatcher → Architecture → Personal MVP → Group MVP → Test`.

### D-012 — Память имеет три горизонта

- HOT — текущая сцена и ограниченный токен-бюджет последних сообщений.
- WARM — недавние raw events с ограниченным retention.
- LONG — структурные Memory Cards.

### D-013 — LONG Memory хранится в универсальных Memory Cards

Для MVP не создаём отдельную таблицу под каждый тип памяти. Основной объект — `memory_cards` с JSONB payload, quality fields, evidence и usage policy.

### D-014 — Active Memory имеет soft cap

Ориентиры MVP:

- Personal: около 150 active LONG cards;
- Group: около 250 active LONG cards.

Это не hard delete limit. При росте запускаются merge, dedup, decay, archive и compaction.

### D-015 — Обычный raw text хранится ограниченное время

MVP default для WARM raw messages — 30 дней. После этого обычный текст может очищаться, а нужное доказательство остаётся как короткий evidence excerpt внутри Memory Card.

### D-016 — Memory Card обязана иметь evidence и quality state

Карточка несёт как минимум confidence, importance, freshness, status, origin и evidence. Inference модели не превращается автоматически в факт.

### D-017 — Память имеет Usage Policy

Одну и ту же память можно разрешить для assist/coach и запретить для roast/callback. Участник группы может отдельно запретить использование конкретной темы для подъёба.

### D-018 — Patterns требуют повторяемости

Ориентир MVP: минимум 3 evidence points в минимум 2 независимых эпизодах. Формулировка pattern должна описывать наблюдаемое поведение, а не ставить диагноз.

### D-019 — Running Joke — отдельный тип памяти

Running jokes хранятся отдельно от generic pattern и имеют callback fatigue, чтобы НеНой не убивал хороший локальный мем бесконечным повторением.

### D-020 — Graph DB не нужна для MVP

Карту строим на PostgreSQL через `memory_cards` + `memory_relations`. `pgvector`/vector retrieval можно добавить позже через абстракцию Retrieval Engine.

### D-021 — Context Builder получает только малый набор релевантной памяти

Обычно 3–8 Memory Cards, а не весь active memory scope. LONG memory budget ориентировочно до 800–1200 токенов на generation request.

### D-022 — Следующая спецификация — Dispatcher

После Personality и Memory следующим фиксируем `DISPATCHER_SPEC.md`: решение `ignore / remember / reply / act / schedule`, Intervention Score, cooldown и социальную инициативу.

### D-023 — Dispatcher возвращает решение, а не текст

Dispatcher не генерирует финальную реплику. Он возвращает структурированный Decision Contract, после чего Context Builder и Response Generator формируют ответ при необходимости.

### D-024 — Group unsolicited intervention использует score + gates

Обычная сумма баллов недостаточна. Порядок: hard blockers → explicit intents → context gates → Intervention Score → cooldown → rate limits → mode selection.

### D-025 — Silence является нормальным primary outcome

`ignore` — не fallback и не ошибка. В Group Mode это стандартный результат для большинства обычных сообщений.

### D-026 — Прямые обращения не блокируются обычным Group cooldown

Cooldown и daily limits регулируют самостоятельные вмешательства. Если участник сам обращается к НеНою, бот должен ответить независимо от unsolicited cooldown.

### D-027 — Group proactive behavior имеет guardrails

Стартовые ориентиры MVP: `12 минут` минимального unsolicited cooldown, soft limit `6` и hard limit `10` самостоятельных вмешательств в сутки. Значения конфигурируемые и будут пересмотрены после Friends Test.

### D-028 — Negative feedback влияет быстрее positive feedback

Несколько игноров или явное «заткнись» быстро уменьшают инициативность и увеличивают cooldown. Позитивные реакции повышают дерзость и инициативу медленно.

### D-029 — Не всё решает LLM

Детерминированно обрабатываются direct mention, reply-to-bot, mute/silence, cooldown, limits, privacy scope, duplicate events и явные системные правила. Lightweight model оценивает социальный контекст. Strong model вызывается только после решения отвечать.

### D-030 — Дорогая модель не принимает решение о собственном вызове

Model routing должен быть cost-aware. Для `ignore` model tier = `none`; memory/classification — лёгкий tier; generation — standard/strong по сложности.

### D-031 — При деградации система становится тише

Если classifier, memory retrieval или strong generation недоступны, Group Mode не должен придумывать callbacks или aggressive roast. Лучше пропустить вмешательство, чем уверенно соврать.

### D-032 — Каждое вмешательство должно быть объяснимо reason codes

Dispatcher сохраняет стабильные `reason_codes`, Intervention Score, mode и policy version. Это основа для analytics и настройки thresholds после живого теста.

### D-033 — Следующий этап — Technical Architecture

После Product Vision, Personality, Memory и Dispatcher переходим к `ARCHITECTURE.md`: реальные компоненты, PostgreSQL schema, runtime v2, model routing, scheduler, queues/jobs, idempotency, observability и end-to-end flows.

### D-034 — Новый runtime v2 строится в `app_v2/`

Унаследованный `app/` из v1 остаётся временным reference implementation. Не переписываем его по частям в новую архитектуру. Entry point v2 будет отдельным.

### D-035 — v2 использует отдельную PostgreSQL database

Для MVP Personal/Group v2 не делят operational DB с v1. Это проще и безопаснее, чем schema-prefix или shared tables.

### D-036 — Web и Worker разделены

`nenoy-v2-web` быстро принимает webhook и сохраняет event. `nenoy-v2-worker` выполняет LLM, memory, dispatch, generation, scheduler и outbox processing.

### D-037 — PostgreSQL используется как durable queue

На MVP не добавляем Redis/RabbitMQ/Kafka/Celery. Events и jobs claims реализуются через PostgreSQL и `FOR UPDATE SKIP LOCKED`.

### D-038 — Telegram webhook не ждёт AI

Webhook выполняет verify → normalize → deduplicate → persist → enqueue → `200 OK`. Цель — быстрый и предсказуемый HTTP path без LLM latency.

### D-039 — Outbox и idempotency обязательны

`telegram_update_id` защищает inbound от duplicate processing. `dedupe_key` в outbox защищает от повторной отправки ответа после retry/restart.

### D-040 — Personality settings на MVP хранятся versioned JSONB

Personal profile — в user context, Group profile — в chat context, Participant adaptation — в membership context. Нормализация в отдельные columns откладывается до появления реального query pressure.

### D-041 — Model routing task-based и конфигурируемый

Используются логические роли `MODEL_CLASSIFIER / MODEL_MEMORY / MODEL_GENERATOR / MODEL_DEEP`, а конкретные модели задаются конфигурацией. Model names не размазываются по бизнес-логике.

### D-042 — Group retrieval никогда не fallback-ится в Personal memory

Любой repository/retrieval API требует scope. Cross-scope transfer в MVP отсутствует.

### D-043 — Каждый AI-вызов имеет usage record

Сохраняются task kind, model, input/output tokens, latency, success и estimated cost. Экономика считается по реальному usage.

### D-044 — При сбоях отвечает более простой контекст, а не выдуманная память

Недоступная Memory/Classifier подсистема не должна провоцировать fake callback. Для unsolicited Group лучше silence; для direct Personal — безопасный reply без LONG-memory утверждений.

### D-045 — Следующий этап — MVP Build Plan

После архитектуры создаём `MVP_BUILD_PLAN.md`: маленькие последовательные задачи для Codex с ограниченным scope, acceptance criteria, tests и обязательным отчётом после выполнения.

## 2026-09-15

### D-046 — Кнопку «🛑 Стоп» под напоминаниями не добавляем

Прямое решение пользователя: кнопка лишняя. Предложение отклонено, а не отложено.

- Не создавать inline-кнопку, callback handler или новую задачу на её реализацию.
- Не считать отсутствие кнопки незавершённой работой.
- Не возвращать предложение в roadmap без нового прямого запроса пользователя.
- Управление остаётся через обычные сообщения и reply на конкретное напоминание.

### D-047 — Остановка напоминаний отделена от mute и доступна участникам текущей группы

Реализовано в [PR #89](https://github.com/Mix13131/nenoybot/pull/89) и [PR #90](https://github.com/Mix13131/nenoybot/pull/90), оба влиты в `v2`.

- Reply `стоп` / `хватит` / `достаточно` на сообщение-напоминание отменяет связанную с ним активную цепочку.
- `НеНой, хватит напоминать @username` отменяет активные цепочки этому адресату в текущей группе.
- `НеНой, останови все напоминания` отменяет все активные цепочки текущей группы.
- Явная stop-команда доступна любому участнику той же группы независимо от автора цепочки. Это заменяет первоначальное ограничение «только создатель» из PR #88; не восстанавливать его при последующих правках.
- Между группами отмена не переносится; Personal memory и Personal actions не затрагиваются.
- Автоматическая остановка по ответу адресата сохраняется для цепочек с `stop_on_reply`.
- Фраза «Горшочек, не вари…» в контексте отмены напоминаний не должна выключать самого НеНоя из беседы.
- Сообщать результат фактической операции: нулевое число отменённых записей не выдавать за успешную отмену.

### D-048 — Повторения ограничены; код, live-операции и проверки фиксируются раздельно

Текущая контрольная точка реализации — merge PR #90, `315304148fac4014e98d9e39b31ebfcb93984763`.

- Минимальный интервал групповых повторяющихся напоминаний — 15 минут; по умолчанию максимум 4 срабатывания цепочки.
- При отмене подавляется связанная ожидающая работа (`events`/`outbox` в `pending`/`retry`). Наличие этого механизма не является доказательством отсутствия гонки с уже обрабатываемой отправкой.
- Аварийная отмена через production-БД — историческая операционная мера, не замена нормальному управлению из чата и не выполненная заново операция при документировании.
- Предыдущие `SUCCESS` и `267 passed, 2 skipped` сохраняются с указанием источника и этапа, а не как результаты нового health-check или тестового запуска.
- Документационная фиксация не даёт оснований обещать отсутствие всех будущих пингов или отмечать live-smoke как PASS.

Подробная точка продолжения, подтверждённые PR и границы проверки: [LIVE_TEST_STATUS.md](LIVE_TEST_STATUS.md).

## 2026-09-19

### D-049 — После acceptance-gate не запускаем бесконечный hardening-loop

Для bounded исправлений перед live-test действует stop-rule:

- сначала закрываются известные воспроизводимые P1/P2, которые ломают реальный acceptance-сценарий;
- затем запускается полный `tests_v2` на isolated PostgreSQL и не более одного bounded delta-review конкретной дельты;
- findings этого review, которые могут создать неверное действие/расписание или иным образом явно нарушают acceptance, закрываются до merge;
- после исправления этих известных findings и зелёного full CI новый широкий аудит той же подсистемы автоматически не запускается;
- новые экзотические или не-блокирующие edge cases фиксируются в backlog по мере появления реального пользовательского сигнала, а не удерживают релиз бесконечно;
- следующий gate после принятого code checkpoint — deploy + bounded live acceptance, а не очередная полировка parser/architecture.

Это stop-rule процесса, а не утверждение, что в коде больше не существует ни одного edge case. Если live-test показывает реальную ошибку, она снова становится предметом отдельной bounded задачи.


## 2026-09-20

### D-050 — Scheduled Action Interpreter отделяет WHAT от WHEN

После live-сигнала с естественными глаголами (`пиши`, `удивляй`, `развлекай`, `спрашивай` и т.п.) новые действия во времени больше не расширяются verb allowlist-ом в reminder regex.

- semantic classifier определяет **WHAT** пользователь поручил НеНою делать;
- deterministic scheduler остаётся авторитетом для **WHEN**: time parsing, timezone, limits, persistence, dedupe и cancellation;
- canonical reminder regex остаётся fail-safe fallback для уже доказанных legacy-команд;
- unsupported external-data capabilities fail closed;
- любой future-action promise требует подтверждённого persisted operation receipt;
- scheduled `generate_text` на fire выполняет `action_instruction`, а не напоминает о том, что пользователь когда-то просил выполнить действие.

Реализация: PR #110, production/code checkpoint `0fe8c260761c446aca9a3fdfd3ae1b00c6a8f05b`. Live acceptance пройден на novel verb `удивляй`.

### D-051 — Friends Test Preflight завершён; следующий gate — 7-дневный продуктовый тест

TASK 27 / #76 считается PASS на основании controlled group activation, live ordinary-message silence-first проверки, live direct mention smoke и текущего privacy/silence/reaction/analytics regression suite.

Это разрешает начать **один controlled Friends Test**, но не broad rollout.

- новые группы не whitelist-ятся автоматически;
- Days 1–2 начинаются с low initiative;
- усиление initiative/roast/callback происходит только по здоровой живой реакции;
- главный North Star — участники кроме владельца, которые сами начинают обращаться к НеНою повторно;
- во время теста новые функции не добавляются без реального live-сигнала;
- после 7 дней выполняется Product Review v0.2, а не автоматический переход к Settings/Closed Beta.

## 2026-09-21

### D-052 — Глубина ответа раскрывается по ходу диалога, а не выгружается сразу

Live-тест НеНой v2 показал конкретный UX-дефект: на обычный экспертный вопрос бот выдал многоэкранный разбор, а запрос пользователя `поясни детальнее, не понятно` интерпретировал как разрешение ещё сильнее расширить тему.

Принятое правило:

- по умолчанию НеНой даёт **минимально достаточный ответ**: главный вывод, 1–2 существенных пояснения и ближайший следующий шаг;
- `не понял`, `поясни`, `объясни` означают прежде всего **сделать предыдущую мысль проще и яснее**, а не увеличить охват;
- `подробнее` раскрывает следующий слой именно текущего тезиса;
- глубокий разбор включается по явному намерению пользователя: `исследуй`, `разбери подробно`, `все нюансы` и эквиваленты;
- соседние нюансы не перечисляются заранее, если они не меняют основной вывод или действие;
- жёсткий общий лимит символов не вводится: это policy управления глубиной, а не механическое обрезание текста;
- критичные safety / medical / legal / compliance оговорки сохраняются, но не должны превращать первый ответ в лекцию без необходимости.

Продуктовый анти-паттерн: **не превращать каждый экспертный вопрос в мини-статью**.

Нормативное описание добавлено в [PERSONALITY_SPEC.md](PERSONALITY_SPEC.md), §2.1. Это live-driven conversational tuning, а не новая функция и не повод для широкого архитектурного hardening.

## 2026-09-25

### D-053 — НеНой получает bounded URL Reader, но не Web Search/browser-agent

Пользователь одобрил узкую способность читать одну явно переданную публичную ссылку как экономически оправданную capability.

Продуктовая граница:
- Personal: URL в текущем сообщении можно считать явной передачей НеНою; URL из replied-to сообщения требует явного намерения прочитать/проверить;
- Group: читать ссылку только при direct mention/reply к НеНою; ambient Group links не должны вызывать outbound fetch;
- одно событие — максимум одна ссылка; несколько ссылок fail closed с просьбой прислать одну;
- direct HTTP extraction — основной путь; Firecrawl допускается только как optional fallback;
- это **не** разрешение на autonomous web search, multi-link crawling, Playwright/browser automation, authenticated/private pages или deep web research.

Безопасность и приватность:
- разрешены только public `http/https` targets на 80/443;
- private/loopback/link-local/non-global IP, unsafe redirects и DNS rebinding блокируются до соединения;
- page content считается untrusted evidence и никогда не переопределяет system/product instructions;
- page body и полный URL не становятся durable telemetry или memory;
- durable telemetry может хранить только sanitized host/status/reason/source/cache/truncation/size metrics без URL path/query и текста страницы.

Экономика должна проверяться по факту использования, а не оцениваться заранее: analytics фиксирует URL request/success/failure/cache/Firecrawl/content size и generator input tokens/cost.

Реализация: TASK 38 / PR #148, squash-merge `68cd8e003765c80111628f5650f9b5b9ca4dd09e`. Full CI — **627 passed** на isolated PostgreSQL 16. Railway web/worker — `SUCCESS`; migration `0006` применена; webhook healthy; `/ready` → 200.

Code/deploy acceptance не заменяет live product acceptance: перед статусом `LIVE PASS` нужен bounded Telegram smoke на реальной публичной статье.
