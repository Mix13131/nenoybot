from __future__ import annotations

try:
    from .config import AppConfig
    from .nenoy_engine import generate_response
    from .prompt_loader import load_all_prompts
except ImportError:  # Allows `python app/main.py`.
    from config import AppConfig
    from nenoy_engine import generate_response
    from prompt_loader import load_all_prompts


EXIT_COMMANDS = {"/exit", "exit", "quit", "выход"}


def run_cli() -> None:
    prompts = load_all_prompts()
    goal = AppConfig.default_goal

    print("НеНойBot запущен. API не подключен: работает локальный тестовый движок.")
    print(f"Промпты загружены: {len(prompts)}.")
    print("Команды: /goal новая цель, /exit")

    if not goal:
        goal_input = input("Цель на сейчас (можно пусто): ").strip()
        goal = goal_input or None
    else:
        print(f"Цель из .env: {goal}")

    while True:
        user_message = input("Ты: ").strip()

        if user_message.lower() in EXIT_COMMANDS:
            print("НеНойBot: Выход. Не растягивай — вернись с отчетом.")
            return

        if user_message.startswith("/goal "):
            goal = user_message.removeprefix("/goal ").strip() or None
            if goal:
                print(f"НеНойBot: Цель обновлена: {goal}. Теперь ближайший шаг.")
            else:
                print("НеНойBot: Цель пустая. Напиши результат, срок и первый шаг.")
            continue

        response = generate_response(user_message, goal=goal)
        print(f"НеНойBot: {response}")


if __name__ == "__main__":
    try:
        run_cli()
    except KeyboardInterrupt:
        print("\nНеНойBot: Остановлено. Вернись с конкретной целью.")
