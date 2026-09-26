"""Indicators — M4.

Pure stdlib functions. Series outputs are aligned to the input (same
length); positions without enough history are `None`. Callers decide the
timeframe by what candles they pass (1-min for Scalp, 5/15-min for Day).
"""

from __future__ import annotations

from collections.abc import Sequence

from src.data.models import SESSION_MINUTES, SESSION_OPEN, Candle

Series = list[float | None]


def ema(values: Sequence[float], period: int) -> Series:
    """EMA seeded with the SMA of the first `period` values."""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder(values: Sequence[float], period: int, start: int) -> Series:
    """Wilder smoothing (average form) of `values[start:]`, seeded with the
    mean of the first `period` of them."""
    out: Series = [None] * len(values)
    seed_end = start + period
    if len(values) < seed_end:
        return out
    prev = sum(values[start:seed_end]) / period
    out[seed_end - 1] = prev
    for i in range(seed_end, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    gains = [0.0] + [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    losses = [0.0] + [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]
    ag, al = _wilder(gains, period, 1), _wilder(losses, period, 1)
    out: Series = [None] * len(closes)
    for i, (g, l) in enumerate(zip(ag, al)):
        if g is None or l is None:
            continue
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """Returns (macd_line, signal_line, histogram)."""
    f, s = ema(closes, fast), ema(closes, slow)
    line: Series = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    first = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig: Series = [None] * first + ema([v for v in line[first:]], signal)  # type: ignore[misc]
    hist: Series = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def true_range(candles: Sequence[Candle]) -> list[float]:
    """TR per candle; index 0 has no prior close and uses high-low."""
    out = [candles[0].high - candles[0].low] if candles else []
    for p, c in zip(candles, candles[1:]):
        out.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> Series:
    """Wilder ATR over TRs from index 1 (first value at index `period`)."""
    return _wilder(true_range(candles), period, 1)


def adx(candles: Sequence[Candle], period: int = 14) -> Series:
    """Wilder ADX; first value at index `2*period - 1`."""
    n = len(candles)
    out: Series = [None] * n
    if n < 2 * period:
        return out
    tr = true_range(candles)
    pdm, mdm = [0.0], [0.0]
    for p, c in zip(candles, candles[1:]):
        up, down = c.high - p.high, p.low - c.low
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
    s_tr, s_p, s_m = (_wilder(x, period, 1) for x in (tr, pdm, mdm))
    dx: list[float] = [0.0] * n
    for i in range(period, n):
        t = s_tr[i]
        pdi = 100 * s_p[i] / t if t else 0.0
        mdi = 100 * s_m[i] / t if t else 0.0
        dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
    return _wilder(dx, period, period)


def supertrend(
    candles: Sequence[Candle], period: int = 10, multiplier: float = 3.0
) -> tuple[Series, list[bool | None]]:
    """Returns (line, is_up). Line sits below price in an uptrend."""
    n = len(candles)
    line: Series = [None] * n
    is_up: list[bool | None] = [None] * n
    a = atr(candles, period)
    upper = lower = 0.0
    up = True
    for i in range(period, n):
        c = candles[i]
        hl2 = (c.high + c.low) / 2
        bu, bl = hl2 + multiplier * a[i], hl2 - multiplier * a[i]  # type: ignore[operator]
        if i == period:
            upper, lower, up = bu, bl, c.close >= hl2
        else:
            pc = candles[i - 1].close
            upper = bu if bu < upper or pc > upper else upper
            lower = bl if bl > lower or pc < lower else lower
            up = c.close >= lower if up else c.close > upper
        line[i], is_up[i] = (lower if up else upper), up
    return line, is_up


def roc(values: Sequence[float], period: int = 10) -> Series:
    """Rate of change in percent."""
    return [
        None if i < period or not values[i - period]
        else (values[i] / values[i - period] - 1) * 100
        for i in range(len(values))
    ]


def vwap(candles: Sequence[Candle]) -> Series:
    """Session VWAP on typical price; resets when the date changes."""
    out: Series = []
    pv = vol = 0.0
    day = None
    for c in candles:
        if c.timestamp.date() != day:
            day, pv, vol = c.timestamp.date(), 0.0, 0.0
        pv += (c.high + c.low + c.close) / 3 * c.volume
        vol += c.volume
        out.append(pv / vol if vol else None)
    return out


def rvol_time_of_day(today: Sequence[Candle], avg_curve: Sequence[float]) -> float | None:
    """Today's cumulative volume vs the average cumulative volume at the
    same session minute (`avg_cumulative_volume_curve`). 1-min candles."""
    if not today or len(avg_curve) != SESSION_MINUTES:
        return None
    ts = today[-1].timestamp
    i = ts.hour * 60 + ts.minute - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)
    if not 0 <= i < SESSION_MINUTES or not avg_curve[i]:
        return None
    return sum(c.volume for c in today) / avg_curve[i]
