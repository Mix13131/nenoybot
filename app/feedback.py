from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Feedback:
    id: int
    chat_id: int
    category: str
    body: str
    mode_at_submit: str
    source_message_id: int | None
    created_at: datetime
    status: str = "submitted"
    notification_status: str = "pending"


def feedback_preview(category: str, body: str) -> str:
    label = "Наблюдение" if category == "observation" else "Помощь"
    return f"Предпросмотр\nТип: {label}\n\n{body}\n\nОтправить это сообщение команде бота?"
