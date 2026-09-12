from __future__ import annotations

from . import topic_runtime as runtime
from . import care_runtime as care
from . import telegram_bot as base


_original_build_reply = base.build_reply


def build_reply(chat_id: int, text: str, store, runtime_state=None) -> str:
    reply = _original_build_reply(chat_id, text, store, runtime_state)
    if (
        isinstance(reply, str)
        and reply.startswith("Обращение №")
        and "ожидает подтверждения доставки" in reply
    ):
        return (
            "🛟 Принял. Передаю сообщение службе заботы.\n"
            "Ответ придёт сюда, в этот чат."
        )
    return reply


base.build_reply = build_reply
care.build_reply = build_reply


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
