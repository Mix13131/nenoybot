from __future__ import annotations

from . import care_runtime as runtime
from . import telegram_bot as base


BUTTON_OBSERVATION_PROJECT = "💡 Идея или отзыв о проекте"
BUTTON_NEED_HELP_TEAM = "🛟 Нужна помощь команды"

# User-facing wording: distinguish feedback about the project from
# a request to the human care team, and both from support by the bot itself.
base.BUTTON_OBSERVATION = BUTTON_OBSERVATION_PROJECT
base.BUTTON_NEED_HELP = BUTTON_NEED_HELP_TEAM
base.FEEDBACK_KEYBOARD = {
    "keyboard": [
        [{"text": BUTTON_OBSERVATION_PROJECT}, {"text": BUTTON_NEED_HELP_TEAM}],
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
