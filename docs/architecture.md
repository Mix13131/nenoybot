# Архитектура НеНойBot

НеНойBot на первом этапе — локальный CLI-проект и Telegram long polling worker. В production-режиме Telegram worker может использовать OpenAI Responses API и Neon/Postgres.

Поток обработки:

```text
User message
↓
State detection
↓
Goal context
↓
Reaction scenario
↓
Combat dictionary style
↓
Final response
```

## Компоненты

## CLI

`app/main.py` запускает диалог в терминале, принимает цель и сообщения пользователя, затем печатает ответ бота.

## Telegram Worker

`app/telegram_bot.py` запускает long polling процесс, читает сообщения из Telegram Bot API и отвечает через локальный движок.

Состояние цели хранится по `chat_id`. Если задан `DATABASE_URL`, используется Neon/Postgres. Если базы нет, включается временная память процесса.

## GPT Client

`app/openai_client.py` собирает системные инструкции из `prompts/`, добавляет цель, память и последние сообщения пользователя, затем вызывает OpenAI Responses API.

Если `OPENAI_API_KEY` не задан или запрос к модели не удался, бот возвращается к локальному keyword-based движку.

## Memory Store

`app/memory_store.py` содержит два хранилища:

- `InMemoryStore` для локальной проверки;
- `PostgresMemoryStore` для Neon/Postgres.

Postgres-слой сам создаёт таблицы `nenoy_user_state` и `nenoy_messages` при старте worker.

## Config

`app/config.py` хранит пути проекта, папку промптов и опциональную цель из `.env`.

## Prompt Loader

`app/prompt_loader.py` загружает markdown-файлы из `prompts/`. Это нужно, чтобы будущий LLM-ассистент мог собирать системную инструкцию из отдельных файлов.

## Engine

`app/nenoy_engine.py` содержит простую локальную логику:

- классификация состояния по ключевым словам;
- выбор реакции;
- генерация короткого ответа;
- учет цели, если она известна.

## Ограничения первого этапа

- нет персональных профилей пользователей;
- нет долгого резюме памяти;
- нет отдельной панели администратора.

Эти части добавляются только после проверки локального каркаса.
