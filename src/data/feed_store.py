"""Live feed store — M7 (DECISIONS #17).

Thread-safe latest-value store fed by `LiveFeed`: the SDK callback thread
counts ticks, the poller thread writes latest LTP/depth. The worker reads
an immutable `FeedSnapshot` once per minute. All ages use local arrival
time, never exchange time, so exchange clock skew cannot make data look
fresh or stale.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Mapping

from src.data.models import DepthSnapshot, Tick

WINDOW = timedelta(minutes=5)
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class SymbolFeedState:
    ltp: float | None
    last_tick_age_s: float | None
    ticks_1m: int
    ticks_5m_avg: float          # ticks in the last 5 min / 5
    observed_s: float | None     # since the first tick; < 5 min → average understated
    depth: DepthSnapshot | None
    depth_age_s: float | None


@dataclass(frozen=True)
class FeedSnapshot:
    symbols: Mapping[str, SymbolFeedState]
    last_any_tick_age_s: float | None
    bad_payloads: int


def _age(now: datetime, at: datetime | None) -> float | None:
    return None if at is None else (now - at).total_seconds()


class FeedStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ltp: dict[str, Tick] = {}
        self._depth: dict[str, tuple[DepthSnapshot, datetime]] = {}
        self._arrivals: dict[str, deque[datetime]] = defaultdict(deque)
        self._last_at: dict[str, datetime] = {}
        self._first_at: dict[str, datetime] = {}
        self._last_any: datetime | None = None
        self._bad = 0

    def count_tick(self, symbol: str, at: datetime) -> None:
        """SDK callback path: one message arrived for `symbol` at local time `at`."""
        with self._lock:
            q = self._arrivals[symbol]
            q.append(at)
            while q and q[0] < at - WINDOW:
                q.popleft()
            if symbol not in self._first_at or at < self._first_at[symbol]:
                self._first_at[symbol] = at
            if symbol not in self._last_at or at > self._last_at[symbol]:
                self._last_at[symbol] = at
            if self._last_any is None or at > self._last_any:
                self._last_any = at

    def put_ltp(self, tick: Tick, received_at: datetime) -> bool:
        """False for a duplicate or out-of-order value (same or older exchange ts)."""
        with self._lock:
            last = self._ltp.get(tick.symbol)
            if last is not None and tick.ts <= last.ts:
                return False
            self._ltp[tick.symbol] = tick
            return True

    def put_depth(self, depth: DepthSnapshot, received_at: datetime) -> bool:
        with self._lock:
            last = self._depth.get(depth.symbol)
            if last is not None and depth.ts <= last[0].ts:
                return False
            self._depth[depth.symbol] = (depth, received_at)
            return True

    def note_bad_payload(self) -> None:
        with self._lock:
            self._bad += 1

    def snapshot(self, now: datetime) -> FeedSnapshot:
        with self._lock:
            out = {}
            for sym in set(self._arrivals) | set(self._ltp) | set(self._depth):
                q = self._arrivals.get(sym, ())
                in_5m = sum(1 for t in q if now - WINDOW <= t <= now)
                in_1m = sum(1 for t in q if now - MINUTE <= t <= now)
                d = self._depth.get(sym)
                tick = self._ltp.get(sym)
                out[sym] = SymbolFeedState(
                    ltp=tick.ltp if tick else None,
                    last_tick_age_s=_age(now, self._last_at.get(sym)),
                    ticks_1m=in_1m,
                    ticks_5m_avg=in_5m / 5,
                    observed_s=_age(now, self._first_at.get(sym)),
                    depth=d[0] if d else None,
                    depth_age_s=_age(now, d[1]) if d else None,
                )
            return FeedSnapshot(MappingProxyType(out), _age(now, self._last_any), self._bad)
