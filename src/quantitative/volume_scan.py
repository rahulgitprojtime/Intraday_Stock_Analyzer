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
import math
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
    atr: float | None = None      # mean true range (high/low/close; Groww omits past opens)


@dataclass(frozen=True)
class ScanFilters:
    min_price: float
    min_avg_daily_volume: float
    min_avg_traded_value: float
    long_only: bool = True
    max_price: float | None = None       # user 2026-09-28: band 250..2500

    @classmethod
    def from_config(cls, filters: dict, long_only: bool = True) -> ScanFilters:
        """From universe.yaml `filters` (+ `scan.long_only`)."""
        return cls(float(filters.get("min_price", 0)), float(filters.get("min_avg_daily_volume", 0)),
                   float(filters.get("min_avg_traded_value", 0)), long_only,
                   float(filters["max_price"]) if filters.get("max_price") else None)

    def price_ok(self, price: float) -> bool:
        return price >= self.min_price and (self.max_price is None or price <= self.max_price)


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    volume_change: float
    day_change_pct: float
    last_price: float
    volume: int
    score: float = 0.0            # M12 pre-rank: movement/volume/liquidity from quotes
    parts: dict | None = None


# M12 (DECISIONS #22): the quote-computable groups, with the engine's weights
# (strategy.yaml engine.weights); the rest need 1-min candles (stage 2).
PRESCORE_WEIGHTS = {"movement": 0.15, "volume": 0.20, "liquidity": 0.05}


def _ramp(x, lo, hi):
    return None if x is None else max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _mean(parts: dict):
    have = [v for v in parts.values() if v is not None]
    return sum(have) / len(have) if have else None


def prescore(q: dict, st: DailyStats, curve: Sequence[float]) -> tuple[float, dict]:
    """Stage-1 score for one quote: MOVEMENT (change vs previous close, range
    vs daily ATR, position in today's range, distance above VWAP), VOLUME
    (time-adjusted volume change, acceleration vs the previous sweep),
    LIQUIDITY (today's traded value). Same ramps as the engine's groups."""
    last, hi, lo = q["last_price"], q.get("high"), q.get("low")
    at = q.get("at")
    frac = expected_fraction(curve, at.time()) if at else None
    change = (last / st.prev_close - 1) * 100
    rng = (hi - lo) / st.atr if hi and lo and st.atr else None
    pos = (last - lo) / (hi - lo) if hi and lo and hi > lo else None
    vw = q.get("average_price")
    vol_change = q["volume"] / (st.avg_volume * frac) if frac else q.get("volume_change")
    accel = None
    if q.get("prev_volume") is not None and at and q.get("prev_at"):
        mins = (at - q["prev_at"]).total_seconds() / 60
        elapsed = (at.hour * 60 + at.minute) - (9 * 60 + 15)
        if mins > 0 and elapsed > 0 and q["volume"] > 0:
            accel = ((q["volume"] - q["prev_volume"]) / mins) / (q["volume"] / elapsed)
    value = q["volume"] * last
    parts = {
        "movement": {"change": _ramp(change, 0, 3), "range": _ramp(rng, 0.3, 1.0),
                     "position": _ramp(pos, 0.5, 1.0),
                     "vwap": _ramp((last / vw - 1) * 100, 0, 1) if vw else None},
        "volume": {"volume_change": _ramp(vol_change, 1, 4), "acceleration": _ramp(accel, 1, 3)},
        "liquidity": {"traded_value": _ramp(math.log10(value), 7.7, 9.0) if value > 0 else None},
    }
    groups = {g: _mean(v) for g, v in parts.items()}
    used = {g: w for g, w in PRESCORE_WEIGHTS.items() if groups[g] is not None}
    score = sum(groups[g] * w for g, w in used.items()) / sum(used.values()) if used else 0.0
    return score, parts


def daily_stats(symbol: str, bars: Sequence[Candle], today: date, sessions: int
                ) -> DailyStats | None:
    """From daily candles strictly before `today` (no look-ahead)."""
    prior = [b for b in bars if b.timestamp.date() < today][-sessions:]
    if not prior:
        return None
    avg_vol = sum(b.volume for b in prior) / len(prior)
    avg_val = sum(b.close * b.volume for b in prior) / len(prior)
    trs, prev = [], None
    for b in prior:
        if b.high is not None and b.low is not None:
            tr = b.high - b.low
            if prev is not None:
                tr = max(tr, abs(b.high - prev), abs(b.low - prev))
            trs.append(tr)
        prev = b.close
    atr = sum(trs) / len(trs) if trs else None
    return DailyStats(symbol, avg_vol, avg_val, prior[-1].close, len(prior), atr)


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
        if (not filters.price_ok(q["last_price"])
                or st.avg_volume < filters.min_avg_daily_volume
                or st.avg_traded_value < filters.min_avg_traded_value):
            continue
        change = (q["last_price"] / st.prev_close - 1) * 100
        if filters.long_only and change <= 0:
            continue
        vol_change = q["volume"] / (st.avg_volume * frac)
        qq = q if q.get("at") else q | {"at": datetime.combine(date.today(), at),
                                         "volume_change": vol_change}
        score, parts = prescore(qq, st, curve)
        out.append(ScanCandidate(q["symbol"], vol_change, change, q["last_price"], q["volume"],
                                 score, parts))
    return sorted(out, key=lambda c: (-c.score, -c.volume_change, c.symbol))


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
