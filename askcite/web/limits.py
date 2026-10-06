"""Limits for the public demo: per visitor, per day, and one question at a time."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime


class QuestionLimiter:
    def __init__(self, per_visitor: int, window_minutes: int, daily_limit: int, clock=time.time):
        self.per_visitor = per_visitor
        self.window = window_minutes * 60
        self.daily_limit = daily_limit
        self.clock = clock
        self._visits: dict[str, deque] = defaultdict(deque)
        self._day: str | None = None
        self._today = 0
        self._lock = threading.Lock()
        self._busy = threading.Lock()  # free tiers allow ~10 requests a minute: answer one question at a time

    def refusal(self, visitor: str) -> str | None:
        """A friendly message if this visitor may not ask now, else None."""
        now = self.clock()
        with self._lock:
            day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
            if day != self._day:
                self._day, self._today = day, 0
            if self._today >= self.daily_limit:
                return ("The demo has answered all the questions it can for today (it runs on a free AI plan). "
                        "Please try a suggested question — those answers are ready — or come back tomorrow.")
            visits = self._visits[visitor]
            while visits and now - visits[0] > self.window:
                visits.popleft()
            if len(visits) >= self.per_visitor:
                wait = int(self.window - (now - visits[0])) // 60 + 1
                return (f"You've asked {self.per_visitor} questions in a short time. Please try again in about "
                        f"{wait} minute(s) — suggested questions still answer instantly.")
        return None

    def record(self, visitor: str) -> None:
        with self._lock:
            self._visits[visitor].append(self.clock())
            self._today += 1

    def try_start(self) -> bool:
        return self._busy.acquire(blocking=False)

    def finish(self) -> None:
        if self._busy.locked():
            self._busy.release()
