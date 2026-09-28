"""Live feed watchdog — M7 (spec 2026-09-28 §6).

The SDK reconnects silently and never tells the caller, so health is
judged by tick age. During the session: no tick for `stale_after_seconds`
→ STALE; for `down_after_seconds` → DOWN and restart (fresh socket
token), at most once per `min_restart_interval_seconds`, backing off to
`slow_restart_interval_seconds` after `max_fast_restarts` restarts
without a tick. Never raises: a failed start/restart leaves status DOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from src.data.models import SESSION_OPEN

SESSION_CLOSE = time(15, 30)
LIVE, STALE, DOWN, OFF = "LIVE", "STALE", "DOWN", "OFF"


@dataclass(frozen=True)
class FeedConfig:
    poll_seconds: float = 1.0
    stale_after_seconds: float = 30
    down_after_seconds: float = 60
    min_restart_interval_seconds: float = 60
    max_fast_restarts: int = 5
    slow_restart_interval_seconds: float = 300

    @classmethod
    def from_dict(cls, d: dict) -> FeedConfig:
        return cls(**{k: type(getattr(cls, k))(v) for k, v in d.items()
                      if k in cls.__dataclass_fields__})


class FeedWatchdog:
    def __init__(self, feed, cfg: FeedConfig) -> None:
        self.feed, self.cfg = feed, cfg
        self.status = DOWN
        self.restarts = 0
        self.failures = 0              # restarts since the last tick
        self.last_error: str | None = None
        self._started_at: datetime | None = None
        self._last_attempt: datetime | None = None

    def _attempt(self, fn, now: datetime) -> None:
        self._started_at = self._last_attempt = now
        try:
            fn()
            self.last_error = None
        except Exception as exc:       # the worker must keep running REST-only
            self.status = DOWN
            self.last_error = f"{type(exc).__name__}: {exc}"

    def start(self, now: datetime) -> None:
        self.status = STALE
        self._attempt(self.feed.start, now)

    def check(self, now: datetime, last_any_tick_age_s: float | None) -> str:
        if not SESSION_OPEN <= now.time() <= SESSION_CLOSE:
            self.status = OFF
            return self.status
        since_start = (now - self._started_at).total_seconds() if self._started_at else 0.0
        age = since_start if last_any_tick_age_s is None else min(last_any_tick_age_s,
                                                                  since_start)
        if last_any_tick_age_s is not None and age <= self.cfg.stale_after_seconds:
            self.status, self.failures = LIVE, 0
        elif age <= self.cfg.down_after_seconds:
            self.status = STALE
        else:
            self.status = DOWN
            wait = (self.cfg.min_restart_interval_seconds
                    if self.failures < self.cfg.max_fast_restarts
                    else self.cfg.slow_restart_interval_seconds)
            if self._last_attempt is None or (now - self._last_attempt).total_seconds() >= wait:
                self.restarts += 1
                self.failures += 1
                self._attempt(self.feed.restart, now)
                self.status = DOWN     # stays DOWN until a tick arrives
        return self.status
