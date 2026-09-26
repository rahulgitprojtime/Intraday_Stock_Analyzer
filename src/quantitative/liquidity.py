"""Liquidity — M6 (spec §12, DECISIONS #14).

Eligibility gate from `universe.yaml` `filters` so an illiquid stock never
ranks on a strong pattern alone. History comes from the same 1-min
request `build_prep` already makes. Spread needs market depth (M7) and is
reported as unavailable. Missing history → unavailable, never invented.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from src.data.models import Candle


@dataclass(frozen=True)
class LiquidityHistory:
    avg_daily_volume: float
    avg_traded_value: float        # rupees/day: sum(close * volume) of 1-min bars
    sessions: int


@dataclass(frozen=True)
class Liquidity:
    eligible: bool | None          # None = unknown (no history)
    avg_daily_volume: float | None
    avg_traded_value: float | None
    current_traded_value: float | None
    spread_pct: float | None       # needs market depth (M7)
    score: float | None
    reason: str


def liquidity_history(minute_candles: Sequence[Candle]) -> LiquidityHistory | None:
    vol: dict = defaultdict(float)
    val: dict = defaultdict(float)
    for c in minute_candles:
        d = c.timestamp.date()
        vol[d] += c.volume
        val[d] += c.close * c.volume
    if not vol:
        return None
    n = len(vol)
    return LiquidityHistory(sum(vol.values()) / n, sum(val.values()) / n, n)


def liquidity_score(avg_traded_value: float | None, min_value: float | None) -> float | None:
    """Log ramp: the configured minimum → 0, ten times it → 100."""
    if avg_traded_value is None or not min_value or avg_traded_value <= 0:
        return None
    x = (math.log10(avg_traded_value) - math.log10(min_value)) * 100
    return max(0.0, min(100.0, x))


def evaluate_liquidity(
    history: LiquidityHistory | None, today: Sequence[Candle], filters: dict
) -> Liquidity:
    bars = [c for c in today if c.is_complete]
    current = sum(c.close * c.volume for c in bars) if bars else None
    if history is None:
        return Liquidity(None, None, None, current, None, None, "liquidity history unavailable")
    fails = []
    min_price = filters.get("min_price")
    if min_price and bars and bars[-1].close < min_price:
        fails.append("price below minimum")
    if history.avg_daily_volume < filters.get("min_avg_daily_volume", 0):
        fails.append("average daily volume below minimum")
    if history.avg_traded_value < filters.get("min_avg_traded_value", 0):
        fails.append("average traded value below minimum")
    reason = "; ".join(fails) if fails else "meets liquidity filters (spread unchecked until M7)"
    score = liquidity_score(history.avg_traded_value, filters.get("min_avg_traded_value"))
    return Liquidity(not fails, history.avg_daily_volume, history.avg_traded_value, current,
                     None, score, reason)
