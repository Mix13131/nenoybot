from __future__ import annotations

import time
from typing import Any


class ReminderScheduler:
    def __init__(self, reminder_repo: Any, *, poll_interval_seconds: float = 5.0) -> None:
        self.reminder_repo = reminder_repo
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)

    def run_once(self) -> bool:
        return self.reminder_repo.fire_due_once() is not None

    def run_forever(self) -> None:
        while True:
            handled = self.run_once()
            if not handled:
                time.sleep(self.poll_interval_seconds)
