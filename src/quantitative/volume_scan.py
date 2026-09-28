"""Market-wide volume scan — M11 (DECISIONS #21).

Picks the active universe: of every liquid, intraday-allowed NSE EQ stock,
the top N by *volume change* = today's volume so far ÷ (20-day average
daily volume × share of a normal day's volume usually traded by now).
The share comes from a market-wide intraday curve measured on real 1-min
data (config/market_volume_curve.json), so stocks quoted a minute apart
compare fairly. Long-only: only stocks above the previous close qualify.
Pure functions; missing stats or quotes are skipped, never guessed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from src.data.models import SESSION_MINUTES, Candle
from src.utils.config import CONFIG_DIR

CURVE_FILE = CONFIG_DIR / "market_volume_curve.json"


@dataclass(frozen=True)
class DailyStats:
    symbol: str
    avg_volume: float
    avg_traded_value: float       # rupees/day, close × volume
    prev_close: float
    sessions: int


@dataclass(frozen=True)
class ScanFilters:
    min_price: float
    min_avg_daily_volume: float
    min_avg_traded_value: float
    long_only: bool = True


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    volume_change: float
    day_change_pct: float
    last_price: float
    volume: int


def daily_stats(symbol: str, bars: Sequence[Candle], today: date, sessions: int
                ) -> DailyStats | None:
    """From daily candles strictly before `today` (no look-ahead)."""
    prior = [b for b in bars if b.timestamp.date() < today][-sessions:]
    if not prior:
        return None
    avg_vol = sum(b.volume for b in prior) / len(prior)
    avg_val = sum(b.close * b.volume for b in prior) / len(prior)
    return DailyStats(symbol, avg_vol, avg_val, prior[-1].close, len(prior))


def load_market_curve() -> list[float]:
    return json.loads(CURVE_FILE.read_text(encoding="utf-8"))["cumulative_fraction"]


def expected_fraction(curve: Sequence[float], at: time) -> float:
    elapsed = (at.hour * 60 + at.minute) - (9 * 60 + 15)
    if elapsed >= SESSION_MINUTES:
        return 1.0
    return curve[max(0, elapsed)]


def rank_volume_change(quotes: Sequence[dict], stats: dict, filters: ScanFilters, at: time,
                       curve: Sequence[float]) -> list[ScanCandidate]:
    frac = expected_fraction(curve, at)
    out = []
    for q in quotes:
        st = stats.get(q["symbol"])
        if st is None or not st.avg_volume or not st.prev_close:
            continue
        if (q["last_price"] < filters.min_price
                or st.avg_volume < filters.min_avg_daily_volume
                or st.avg_traded_value < filters.min_avg_traded_value):
            continue
        change = (q["last_price"] / st.prev_close - 1) * 100
        if filters.long_only and change <= 0:
            continue
        out.append(ScanCandidate(q["symbol"], q["volume"] / (st.avg_volume * frac), change,
                                 q["last_price"], q["volume"]))
    return sorted(out, key=lambda c: (-c.volume_change, c.symbol))


class ActiveSet:
    """Top-N by volume change, with a minimum stay to limit churn; pinned
    symbols (open paper positions) never drop."""

    def __init__(self, top_n: int, min_stay_minutes: float) -> None:
        self.top_n, self.min_stay = top_n, min_stay_minutes
        self._entered: dict[str, datetime] = {}

    def update(self, now: datetime, ranked: Sequence[ScanCandidate], pinned: set) -> list[str]:
        top = [c.symbol for c in ranked[: self.top_n]]
        keep = [s for s, at in self._entered.items()
                if s not in top and (now - at).total_seconds() / 60 < self.min_stay]
        members = set(top) | set(keep) | set(pinned)
        for s in members:
            self._entered.setdefault(s, now)
        for s in list(self._entered):
            if s not in members:
                del self._entered[s]
        order = [c.symbol for c in ranked if c.symbol in members]
        return order + sorted(members - set(order))
