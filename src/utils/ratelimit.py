"""Thread-safe call spacing — M13 fast scan (DECISIONS #23).

Groww's Live Data limit is 10 calls/s and 300/min (docs/groww_api_notes.md);
parallel quote workers share one limiter so bursts never exceed it.
"""

from __future__ import annotations

import threading
import time as _time
from collections.abc import Callable


class RateLimiter:
    def __init__(self, per_second: float, clock: Callable[[], float] = _time.monotonic,
                 sleep: Callable[[float], None] = _time.sleep) -> None:
        self.gap = 1.0 / per_second
        self._clock, self._sleep = clock, sleep
        self._next = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Reserve the next slot (under the lock), then wait for it outside it."""
        with self._lock:
            now = self._clock()
            slot = now if self._next is None or self._next <= now else self._next
            self._next = slot + self.gap
        wait = slot - self._clock()
        if wait > 0:
            self._sleep(wait)
