from __future__ import annotations

from . import topic_runtime as runtime
from . import care_runtime as care
from . import telegram_bot as base


_original_build_reply = base.build_reply
_topic_deliver_pending_feedback = runtime.deliver_pending_feedback


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


def deliver_pending_feedback(api: base.TelegramAPI, store) -> None:
    config = care.CARE.get_config()
    if config is None:
        return _topic_deliver_pending_feedback(api, store)

    for feedback in store.pending_feedback():
        category_label = (
            "🛟 Нужна помощь команды"
            if feedback.category == "help"
            else "💡 Идея или отзыв о проекте"
        )
        thread_id = runtime._ensure_topic(
            api,
            config.chat_id,
            feedback.chat_id,
            config.message_thread_id,
        )
        notice = (
            f"{category_label} · обращение №{feedback.id}\n"
            f"Время: {feedback.created_at.isoformat()}\n"
            f"Режим: {feedback.mode_at_submit}\n\n"
            f"{feedback.body}\n\n"
            "↩️ Ответь Reply на это сообщение — бот доставит ответ пользователю."
        )
        status, message_id = base.deliver_telegram(
            lambda: care._send_group_message(
                api,
                config.chat_id,
                notice,
                message_thread_id=thread_id,
            )
        )
        store.mark_feedback_notification(feedback.id, status, message_id)
        if status == "confirmed" and message_id is not None:
            care.CARE.link(
                feedback.id,
                care_message_id=message_id,
                direction="user_to_care",
            )


base.build_reply = build_reply
care.build_reply = build_reply
base.deliver_pending_feedback = deliver_pending_feedback
care.deliver_pending_feedback = deliver_pending_feedback
runtime.deliver_pending_feedback = deliver_pending_feedback


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
