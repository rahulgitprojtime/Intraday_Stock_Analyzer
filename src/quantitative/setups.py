"""Long setup detectors — M4b (DECISIONS.md #11, #13).

Each detector looks at today's *completed* candles of one timeframe
(1-min for Scalp, 5/15-min for Day) and returns a `SetupSignal`. States:

- NONE       setup not present
- FORMING    conditions building; trigger not yet crossed
- TRIGGERED  trigger crossed and price still holding it
- EXTENDED   triggered but already more than `ext` beyond the trigger
- FAILED     triggered then lost the trigger (or invalidated)

`ext` is an absolute price distance the caller derives from ATR. Levels are
used internally only; `detail` text never quotes prices (DECISIONS #11).
Thresholds are illustrative defaults, not tuned (M10 validates them).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from src.data.models import SESSION_OPEN, Candle
from src.indicators.core import ema, vwap
from src.quantitative.daily_prep import DailyPrep

NEAR_PCT = 0.75      # FORMING when within this % below the trigger
TOUCH_TOL = 0.001    # pullback "touch" tolerance above support (0.1%)


class SetupState(str, Enum):
    NONE = "NONE"
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SetupSignal:
    name: str
    state: SetupState
    detail: str = ""


def _done(candles: Sequence[Candle]) -> list[Candle]:
    return [c for c in candles if c.is_complete]


def _cross_state(
    closes: Sequence[float],
    levels: Sequence[float | None],
    ext: float,
    ref: float,
    lookback: int | None = None,
) -> SetupState:
    """State of an upward cross of `levels` by `closes`. `ref` is the price
    before closes[0] (compared against levels[0])."""
    if not closes or levels[-1] is None:
        return SetupState.NONE
    first = 0 if lookback is None else max(0, len(closes) - lookback)
    crossed = False
    prev, prev_lvl = ref, levels[0]
    for i, (c, lvl) in enumerate(zip(closes, levels)):
        if lvl is not None and prev_lvl is not None and i >= first:
            if c > lvl and prev <= prev_lvl:
                crossed = True
        prev, prev_lvl = c, lvl
    last, lvl = closes[-1], levels[-1]
    if crossed:
        if last < lvl:
            return SetupState.FAILED
        return SetupState.EXTENDED if last - lvl > ext else SetupState.TRIGGERED
    if lvl * (1 - NEAR_PCT / 100) <= last <= lvl:
        return SetupState.FORMING
    return SetupState.NONE


def _pullback_state(
    candles: Sequence[Candle],
    touch: Sequence[float | None],
    support: Sequence[float | None],
    trend_ok,
    ext: float,
    lookback: int = 5,
) -> SetupState:
    """Most recent bar (within `lookback`) whose low tagged `touch` while
    closing at/above `support`, in an uptrend per `trend_ok(i)`. Then:
    close above the touch bar's high -> TRIGGERED; close below support ->
    FAILED; otherwise FORMING."""
    n = len(candles)
    t = None
    for i in range(n - 1, max(-1, n - 1 - lookback), -1):
        c, tv, sv = candles[i], touch[i], support[i]
        if tv is None or sv is None:
            continue
        if c.low <= tv * (1 + TOUCH_TOL) and c.close >= sv:
            t = i
            break
    if t is None or not trend_ok(t):
        return SetupState.NONE
    high = candles[t].high
    state = SetupState.FORMING
    for i in range(t + 1, n):
        if support[i] is not None and candles[i].close < support[i]:
            return SetupState.FAILED
        if candles[i].close > high:
            state = SetupState.TRIGGERED
    if state is SetupState.TRIGGERED and candles[-1].close - high > ext:
        return SetupState.EXTENDED
    return state


def opening_range_breakout(candles: Sequence[Candle], minutes: int, ext: float) -> SetupSignal:
    name = f"ORB{minutes}"
    bars = _done(candles)
    if not bars:
        return SetupSignal(name, SetupState.NONE)
    end = datetime.combine(bars[0].timestamp.date(), SESSION_OPEN) + timedelta(minutes=minutes)
    rng = [c for c in bars if c.timestamp < end]
    after = [c for c in bars if c.timestamp >= end]
    covered = rng and rng[-1].timestamp + timedelta(minutes=rng[-1].timeframe_minutes) >= end
    if not covered:
        return SetupSignal(name, SetupState.NONE, "opening range not complete")
    level = max(c.high for c in rng)
    # Range closes are <= its high, so the first post-range bar can cross.
    closes = [c.close for c in after] or [rng[-1].close]
    state = _cross_state(closes, [level] * len(closes), ext, ref=level)
    return SetupSignal(name, state, f"{minutes}-min opening range breakout")


def pdh_breakout(candles: Sequence[Candle], prep: DailyPrep, ext: float) -> SetupSignal:
    bars = _done(candles)
    closes = [c.close for c in bars]
    level = prep.prev_high
    state = _cross_state(closes, [level] * len(closes), ext, bars[0].open) if bars else SetupState.NONE
    return SetupSignal("PDH", state, "breakout above previous day high")


def vwap_reclaim(candles: Sequence[Candle], ext: float, lookback: int = 10) -> SetupSignal:
    bars = _done(candles)
    if not bars:
        return SetupSignal("VWAP_RECLAIM", SetupState.NONE)
    vw = vwap(bars)
    closes = [c.close for c in bars]
    state = _cross_state(closes, vw, ext, bars[0].open, lookback)
    return SetupSignal("VWAP_RECLAIM", state, "reclaimed VWAP from below")


def vwap_pullback(candles: Sequence[Candle], ext: float, min_above: float = 0.6) -> SetupSignal:
    bars = _done(candles)
    vw = vwap(bars)

    def trend_ok(t: int) -> bool:
        prior = [(c.close, v) for c, v in zip(bars[max(0, t - 10):t], vw[max(0, t - 10):t])]
        return bool(prior) and sum(1 for c, v in prior if v is not None and c > v) / len(prior) >= min_above

    state = _pullback_state(bars, vw, vw, trend_ok, ext)
    return SetupSignal("VWAP_PULLBACK", state, "held VWAP on pullback in uptrend")


def ema_pullback(candles: Sequence[Candle], ext: float) -> SetupSignal:
    bars = _done(candles)
    closes = [c.close for c in bars]
    e9, e20 = ema(closes, 9), ema(closes, 20)

    def trend_ok(t: int) -> bool:
        return e9[t] is not None and e20[t] is not None and e9[t] > e20[t]

    state = _pullback_state(bars, e9, e20, trend_ok, ext)
    return SetupSignal("EMA_PULLBACK", state, "pullback to EMA9 with EMA9 above EMA20")


def narrow_cpr_trend(
    candles: Sequence[Candle], prep: DailyPrep, ext: float, max_width_pct: float = 0.25
) -> SetupSignal:
    name = "NARROW_CPR"
    bars = _done(candles)
    if not bars or prep.cpr_width_pct > max_width_pct:
        return SetupSignal(name, SetupState.NONE)
    top, bottom = prep.cpr_top, prep.cpr_bottom
    last = bars[-1].close
    if last > top:
        state = SetupState.EXTENDED if last - top > ext else SetupState.TRIGGERED
    elif last >= bottom:
        was_above = any(c.close > top for c in bars[:-1])
        state = SetupState.FAILED if was_above else SetupState.FORMING
    else:
        state = SetupState.NONE
    return SetupSignal(name, state, "narrow CPR: trend-day bias above CPR")


def gap_and_go(
    candles: Sequence[Candle], prep: DailyPrep, ext: float, min_gap_pct: float = 1.0
) -> SetupSignal:
    name = "GAP_AND_GO"
    bars = _done(candles)
    if not bars or not prep.prev_close:
        return SetupSignal(name, SetupState.NONE)
    first = bars[0]
    gap_pct = (first.open / prep.prev_close - 1) * 100
    if gap_pct < min_gap_pct:
        return SetupSignal(name, SetupState.NONE)
    detail = f"gap up {gap_pct:.1f}%"
    if bars[-1].close <= prep.prev_close:
        return SetupSignal(name, SetupState.FAILED, detail + ", gap filled")
    rest = bars[1:]
    if not rest:
        return SetupSignal(name, SetupState.FORMING, detail)
    closes = [c.close for c in rest]
    state = _cross_state(closes, [first.high] * len(closes), ext, first.close)
    if state is SetupState.NONE:
        state = SetupState.FORMING  # gap intact, waiting for the first-bar high
    return SetupSignal(name, state, detail)


def relative_strength(
    candles: Sequence[Candle], index_candles: Sequence[Candle], min_rs_pct: float = 0.5
) -> SetupSignal:
    """Stock % change since open minus the index's, same window."""
    name = "RS_VS_NIFTY"
    bars, idx = _done(candles), _done(index_candles)
    if not bars or not idx or not bars[0].open or not idx[0].open:
        return SetupSignal(name, SetupState.NONE)
    stock = (bars[-1].close / bars[0].open - 1) * 100
    index = (idx[-1].close / idx[0].open - 1) * 100
    rs = stock - index
    if stock <= 0 or rs <= 0:
        return SetupSignal(name, SetupState.NONE)
    state = SetupState.TRIGGERED if rs >= min_rs_pct else SetupState.FORMING
    return SetupSignal(name, state, f"outperforming NIFTY by {rs:.1f} pts since open")


def momentum_burst(
    candles: Sequence[Candle],
    lookback: int = 20,
    body_mult: float = 2.0,
    vol_mult: float = 2.0,
    hold: int = 3,
) -> SetupSignal:
    """1-min: a bullish bar with body and volume >= `mult` x the prior
    `lookback` average, closing in the top quarter of its range. Stays
    TRIGGERED for `hold` bars while price holds the burst bar's midpoint."""
    name = "MOMENTUM_BURST"
    bars = _done(candles)
    n = len(bars)
    for b in range(n - 1, max(lookback - 1, n - 1 - hold), -1):
        c = bars[b]
        prior = bars[b - lookback:b]
        avg_body = sum(abs(p.close - p.open) for p in prior) / lookback
        avg_vol = sum(p.volume for p in prior) / lookback
        rng = c.high - c.low
        if (
            c.close > c.open
            and rng > 0
            and (c.close - c.low) / rng >= 0.75
            and c.close - c.open >= body_mult * avg_body
            and c.volume >= vol_mult * avg_vol
        ):
            mid = (c.high + c.low) / 2
            state = SetupState.FAILED if bars[-1].close < mid else SetupState.TRIGGERED
            return SetupSignal(name, state, f"{c.volume / avg_vol:.1f}x volume burst" if avg_vol else "")
    return SetupSignal(name, SetupState.NONE)
