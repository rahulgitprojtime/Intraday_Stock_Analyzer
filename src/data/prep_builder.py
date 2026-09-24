"""Daily prep builder — M3.

One 1-min history request per stock (<= 30-day window, a single Groww
call) yields both the daily candles for CPR/NR7/ATR and the per-minute
volume curve for time-of-day RVOL (DECISIONS.md #12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from src.data.candles import aggregate_daily
from src.data.models import HistoricalCandleRequest, Instrument
from src.quantitative.daily_prep import (
    DailyPrep,
    avg_cumulative_volume_curve,
    compute_daily_prep,
)

LOOKBACK_CALENDAR_DAYS = 29   # stays inside the 30-day 1-min window
VOLUME_CURVE_SESSIONS = 20


@dataclass(frozen=True)
class PrepResult:
    prep: DailyPrep
    volume_curve: list[float]
    sessions: int


def build_prep(adapter, instrument: Instrument, today: date) -> PrepResult | None:
    """`adapter` is any BrokerAdapter. Uses only sessions before `today`;
    returns None when there is no history."""
    yesterday = today - timedelta(days=1)
    request = HistoricalCandleRequest(
        instrument=instrument,
        start_time=datetime.combine(today - timedelta(days=LOOKBACK_CALENDAR_DAYS), time(9, 15)),
        end_time=datetime.combine(yesterday, time(15, 30)),
        interval_minutes=1,
    )
    minutes = [c for c in adapter.get_historical_candles(request) if c.timestamp.date() < today]
    daily = aggregate_daily(minutes)
    if not daily:
        return None
    recent = {d.timestamp.date() for d in daily[-VOLUME_CURVE_SESSIONS:]}
    curve = avg_cumulative_volume_curve([c for c in minutes if c.timestamp.date() in recent])
    return PrepResult(compute_daily_prep(daily), curve, len(daily))
