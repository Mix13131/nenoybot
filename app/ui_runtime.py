from __future__ import annotations

from . import care_runtime as runtime
from . import telegram_bot as base


BUTTON_NEED_HELP_TEAM = "🛟 Нужна помощь команды"

# User-facing wording: distinguish a request to the human care team
# from asking the bot itself for support.
base.BUTTON_NEED_HELP = BUTTON_NEED_HELP_TEAM
base.FEEDBACK_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_OBSERVATION}, {"text": BUTTON_NEED_HELP_TEAM}],
        [{"text": "Отмена"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": False,
}


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
