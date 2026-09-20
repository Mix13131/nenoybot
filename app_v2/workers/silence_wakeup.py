from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class GroupSilenceWakeupWorker:
    service: Any
    interval_seconds: int = 60
    last_run_at: datetime | None = None

    def run_if_due(self, *, now: datetime | None = None) -> bool | None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.last_run_at is not None:
            next_run = self.last_run_at + timedelta(seconds=self.interval_seconds)
            if current < next_run:
                return None

        handled = bool(self.service.run_once(now=current))
        self.last_run_at = current
        return handled
