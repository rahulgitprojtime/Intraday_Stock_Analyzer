"""Daily prep — M3.

Pre-market context computed once per day from *completed* prior sessions:
prior-day levels, CPR, NR7 / inside day, ATR%, and the average
cumulative-volume-by-minute curve used for time-of-day RVOL.

Levels here feed setup detection internally; they are never shown as
price levels on candidate cards (DECISIONS.md #11). Pure functions over
`Candle`s — callers must pass only sessions strictly before today.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time

from src.data.models import Candle

SESSION_OPEN = time(9, 15)
SESSION_MINUTES = 375  # 09:15-15:30 NSE cash session


@dataclass(frozen=True)
class DailyPrep:
    prev_high: float
    prev_low: float
    prev_close: float
    pivot: float
    cpr_top: float
    cpr_bottom: float
    cpr_width_pct: float           # narrow CPR hints at a trend day
    is_nr7: bool | None            # None = fewer than 7 sessions
    is_inside_day: bool | None     # None = fewer than 2 sessions
    atr: float | None              # Wilder ATR on daily candles
    atr_pct: float | None          # ATR as % of prior close


def wilder_atr(daily: Sequence[Candle], period: int = 14) -> float | None:
    """Wilder ATR: seed with the mean of the first `period` true ranges,
    then smooth. Needs `period + 1` candles (TR uses the prior close)."""
    if len(daily) < period + 1:
        return None
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(daily, daily[1:])
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_daily_prep(daily: Sequence[Candle], atr_period: int = 14) -> DailyPrep:
    """`daily` is ordered oldest -> newest; the last candle is the prior day."""
    if not daily:
        raise ValueError("daily prep needs at least one completed session")
    prev = daily[-1]
    h, l, c = prev.high, prev.low, prev.close
    pivot = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pivot - bc
    top, bottom = max(tc, bc), min(tc, bc)

    is_nr7 = None
    if len(daily) >= 7:
        last7 = [d.high - d.low for d in daily[-7:]]
        is_nr7 = last7[-1] <= min(last7[:-1])

    is_inside = None
    if len(daily) >= 2:
        before = daily[-2]
        is_inside = h <= before.high and l >= before.low

    atr = wilder_atr(daily, atr_period)
    return DailyPrep(
        prev_high=h,
        prev_low=l,
        prev_close=c,
        pivot=pivot,
        cpr_top=top,
        cpr_bottom=bottom,
        cpr_width_pct=(top - bottom) / pivot * 100,
        is_nr7=is_nr7,
        is_inside_day=is_inside,
        atr=atr,
        atr_pct=None if atr is None else atr / c * 100,
    )


def _minute_index(candle: Candle) -> int:
    ts = candle.timestamp
    return (ts.hour * 60 + ts.minute) - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)


def avg_cumulative_volume_curve(minute_candles: Sequence[Candle]) -> list[float]:
    """Average cumulative volume at each session minute across the given
    days (1-min candles). Missing minutes carry the day's running total
    forward. Returns [] for no data; else `SESSION_MINUTES` values."""
    per_day: dict = defaultdict(lambda: [0] * SESSION_MINUTES)
    for c in minute_candles:
        i = _minute_index(c)
        if 0 <= i < SESSION_MINUTES:
            per_day[c.timestamp.date()][i] += c.volume
    if not per_day:
        return []
    totals = [0.0] * SESSION_MINUTES
    for vols in per_day.values():
        running = 0
        for i, v in enumerate(vols):
            running += v
            totals[i] += running
    n = len(per_day)
    return [t / n for t in totals]
