"""Candle transforms — M3.

Pure functions over 1-min `Candle`s (naive IST timestamps, as returned by
the adapter): daily aggregation, N-min resampling aligned to the 09:15
session open, and staleness checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from src.data.models import SESSION_OPEN, Candle


def _merge(group: Sequence[Candle], timestamp: datetime, minutes: int, complete: bool) -> Candle:
    first = group[0]
    return Candle(
        instrument=first.instrument,
        timeframe_minutes=minutes,
        timestamp=timestamp,
        open=first.open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
        is_complete=complete,
    )


def aggregate_daily(minute_candles: Sequence[Candle]) -> list[Candle]:
    """One 1440-min candle per trading date, oldest first."""
    by_day: dict = {}
    for c in sorted(minute_candles, key=lambda c: c.timestamp):
        by_day.setdefault(c.timestamp.date(), []).append(c)
    return [
        _merge(group, datetime.combine(d, SESSION_OPEN), 1440, True)
        for d, group in by_day.items()
    ]


def resample(
    minute_candles: Sequence[Candle], minutes: int, now: datetime | None = None
) -> list[Candle]:
    """Buckets start at 09:15 + k*minutes. With `now`, a bucket whose end
    is after `now` is flagged `is_complete=False`."""
    buckets: dict = {}
    for c in sorted(minute_candles, key=lambda c: c.timestamp):
        open_ts = datetime.combine(c.timestamp.date(), SESSION_OPEN)
        k = int((c.timestamp - open_ts).total_seconds() // 60) // minutes
        buckets.setdefault(open_ts + timedelta(minutes=k * minutes), []).append(c)
    out = []
    for start, group in buckets.items():
        complete = (now is None or now >= start + timedelta(minutes=minutes)) and all(
            c.is_complete for c in group
        )
        out.append(_merge(group, start, minutes, complete))
    return out


def is_stale(last_ts: datetime | None, now: datetime, max_age_seconds: int = 120) -> bool:
    """True when the newest 1-min candle's open is older than the threshold."""
    return last_ts is None or (now - last_ts).total_seconds() > max_age_seconds
