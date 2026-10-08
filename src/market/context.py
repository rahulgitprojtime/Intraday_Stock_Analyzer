"""Market context (DECISIONS #22).

`market_context_score` from NIFTY % change since the open, linear ramp
(temporary baseline). Missing or stale NIFTY → unavailable (excluded from
the blend), never a neutral default. Future inputs: BANKNIFTY, breadth,
volatility regime, sector indices (M8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.data.models import Candle


@dataclass(frozen=True)
class MarketContext:
    status: str                      # "available" | "unavailable"
    score: float | None
    nifty_change_pct: float | None
    source: str = "NIFTY"
    reason: str = ""


def nifty_context(
    index_bars: Sequence[Candle],
    as_of: datetime,
    stale_after_seconds: float,
    ramp_pct: Sequence[float] = (-0.5, 0.5),
) -> MarketContext:
    bars = [c for c in index_bars if c.is_complete]
    if not bars or not bars[0].open:
        return MarketContext("unavailable", None, None, reason="no NIFTY candles")
    last = bars[-1]
    age = (as_of - (last.timestamp + timedelta(minutes=last.timeframe_minutes))).total_seconds()
    if age > stale_after_seconds:
        return MarketContext("unavailable", None, None, reason="NIFTY data stale")
    chg = (last.close / bars[0].open - 1) * 100
    lo, hi = ramp_pct
    score = max(0.0, min(100.0, (chg - lo) / (hi - lo) * 100))
    return MarketContext("available", score, chg, reason=f"NIFTY {chg:+.2f}% since open")
