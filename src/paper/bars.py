"""Bar helpers for strategies — DECISIONS #30. SIMULATION ONLY.

`BarAggregator` turns 1-min bars into N-min bars aligned to 09:15 as they
close (incrementally: a year-long backtest cannot re-resample every
minute). A bucket closes when its time is over (`flush(as_of)`) or when a
later bar arrives (data gap). `bullish_reversal` classifies the candle
patterns the intraday playbook uses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.data.models import SESSION_OPEN
from src.paper.orders import Bar


def _merge(group: list[Bar], start: datetime) -> Bar:
    return Bar(group[0].symbol, start, group[0].open, max(b.high for b in group),
               min(b.low for b in group), group[-1].close, sum(b.volume for b in group))


class BarAggregator:
    def __init__(self, minutes: int) -> None:
        self.minutes = minutes
        self.done: list[Bar] = []
        self._cur: list[Bar] = []
        self._start: datetime | None = None

    def _bucket(self, ts: datetime) -> datetime:
        open_ts = datetime.combine(ts.date(), SESSION_OPEN)
        k = int((ts - open_ts).total_seconds() // 60) // self.minutes
        return open_ts + timedelta(minutes=k * self.minutes)

    def _close(self) -> Bar:
        b = _merge(self._cur, self._start)
        self.done.append(b)
        self._cur, self._start = [], None
        return b

    def add(self, bar: Bar) -> Bar | None:
        """Add a closed 1-min bar; returns a bucket this closed, if any."""
        start = self._bucket(bar.ts)
        closed = self._close() if self._cur and start != self._start else None
        self._start = start
        self._cur.append(bar)
        return closed

    def flush(self, as_of: datetime) -> Bar | None:
        """Close the current bucket once its time is over."""
        if self._cur and as_of >= self._start + timedelta(minutes=self.minutes):
            return self._close()
        return None

    def seed(self, bars: list[Bar]) -> None:
        """Completed history (e.g. the prior session) for indicator warm-up."""
        for b in bars:
            self.add(b)
        if self._cur:
            self._close()

    def today(self, day: date) -> list[Bar]:
        return [b for b in self.done if b.ts.date() == day]


def bullish_reversal(prev: Bar | None, cur: Bar) -> str | None:
    """ENGULFING: a red bar's body engulfed by a green body. HAMMER: lower
    wick >= 2x body, upper wick <= body, close in the top 40% of the range.
    PIN_BAR: lower wick >= 2/3 of the range. Else None."""
    rng = cur.high - cur.low
    if rng <= 0:
        return None
    body = abs(cur.close - cur.open)
    lower = min(cur.open, cur.close) - cur.low
    upper = cur.high - max(cur.open, cur.close)
    if prev is not None and prev.close < prev.open and cur.close > cur.open \
            and cur.close >= prev.open and cur.open <= prev.close:
        return "ENGULFING"
    if lower >= 2 * body and upper <= body and cur.close >= cur.low + 0.6 * rng:
        return "HAMMER"
    if lower >= rng * 2 / 3:
        return "PIN_BAR"
    return None
