# НеНойBot

НеНойBot — локальный каркас AI-тренера по дисциплине, фокусу и результату.

На первом этапе проект работает как простой CLI без внешнего LLM API, Telegram, базы данных и no-code интеграций. Главная задача этого этапа — подготовить правильную файловую архитектуру, системную инструкцию, боевой словарь, сценарии реакций и минимальный запуск.

## Назначение

НеНойBot помогает пользователю:

- удерживать цель;
- возвращаться к ближайшему действию;
- пресекать прокрастинацию;
- дробить задачи до шага на 5-20 минут;
- фиксировать срок и отчет;
- не превращать сложность в бесконечное нытье.

## Структура проекта

```text
nenoybot/
  README.md
  .gitignore
  .env.example
  requirements.txt

  prompts/
    system_prompt.md
    state_classifier.md
    combat_dictionary.md
    reaction_scenarios.md
    response_algorithm.md

  docs/
    architecture.md
    usage.md
    roadmap.md

  examples/
    examples.md
    user_dialogues.md
    test_cases.md

  app/
    main.py
    config.py
    prompt_loader.py
    nenoy_engine.py

  tests/
    test_prompt_loader.py
    test_nenoy_engine.py
```

## Быстрый старт

```bash
cd /Users/tony/Projects/codex-workspace/nenoybot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app/main.py
```

Для выхода из CLI:

```text
/exit
```

Для смены цели внутри CLI:

```text
/goal Закончить черновик сегодня до 18:00
```

## Проверка

```bash
pytest
```

Минимальная проверка без установки зависимостей:

```bash
python3 -m py_compile app/*.py
printf 'Закончить README сегодня\nпотом сделаю\n/exit\n' python3 app/main.py
```

## Что уже есть

- системный промпт НеНойBot;
- классификатор состояний;
- боевой словарь;
- сценарии реакций;
- алгоритм ответа;
- эталонные диалоги;
- загрузчик markdown-промптов;
- простой keyword-based движок;
- локальный CLI-запуск;
- базовые тесты.

## Что дальше

1. Проверить стиль ответов на реальных диалогах.
2. Подключить LLM API отдельным слоем, не ломая локальный движок.
3. После проверки CLI добавить Telegram-бота или Make/webhook-интеграцию.
