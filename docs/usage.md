# Локальный запуск

## 1. Перейти в проект

```bash
cd /Users/tony/Projects/codex-workspace/nenoybot
```

## 2. Создать виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Установить зависимости

```bash
pip install -r requirements.txt
```

## 4. Настроить `.env`

```bash
cp .env.example .env
```

Можно оставить `NENOYBOT_DEFAULT_GOAL` пустым. Тогда CLI спросит цель при запуске.

## 5. Запустить CLI

```bash
python app/main.py
```

Команды внутри CLI:

```text
/goal новая цель
/exit
```

## 6. Запустить тесты

```bash
pytest
```

## Telegram online worker

Создай бота через `@BotFather`, затем добавь токен в `.env`:

```text
TELEGRAM_BOT_TOKEN=123456:token
```

Запуск:

```bash
python -m app.telegram_bot
```

Команды:

```text
/start
/goal результат + срок
/clear_goal
```

Для хостинга используй тот же start command:

```bash
python -m app.telegram_bot
```

## Пример запуска

```text
НеНойBot запущен. API не подключен: работает локальный тестовый движок.
Цель на сейчас: Закончить черновик лендинга сегодня
Ты: потом сделаю
НеНойBot: Потом отменяется. Цель: Закончить черновик лендинга сегодня. Открой задачу и сделай 10 минут. Напиши время старта.
```
