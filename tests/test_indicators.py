from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.indicators.core import (
    adx,
    atr,
    ema,
    macd,
    roc,
    rsi,
    rvol_time_of_day,
    supertrend,
    vwap,
)

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)
T0 = datetime(2026, 9, 25, 9, 15)


def bar(i, o, h, l, c, v=100, start=T0):
    return Candle(INST, 1, start + timedelta(minutes=i), o, h, l, c, v)


def trend(n, step=1.0, start=100.0):
    """Steady uptrend (step>0) or downtrend (step<0) 1-min bars."""
    out = []
    for i in range(n):
        o = start + i * step
        c = o + step
        out.append(bar(i, o, max(o, c) + 0.2, min(o, c) - 0.2, c))
    return out


def test_ema_seeds_with_sma_then_smooths():
    out = ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)   # SMA(1,2,3)
    assert out[3] == pytest.approx(3.0)   # 0.5*4 + 0.5*2
    assert out[4] == pytest.approx(4.0)


def test_ema_short_input_is_all_none():
    assert ema([1, 2], 3) == [None, None]


def test_rsi_wilder_reference():
    closes = [10, 11, 12, 11, 12, 13]
    out = rsi(closes, 3)
    assert out[:3] == [None] * 3
    # first avg: gains (1,1,0)/3, losses (0,0,1)/3 -> RS 2 -> 66.67
    assert out[3] == pytest.approx(100 - 100 / 3)
    # Wilder: avg_gain=(2/3*2+1)/3=7/9, avg_loss=(1/3*2)/3=2/9 -> RS 3.5
    assert out[4] == pytest.approx(100 - 100 / 4.5)


def test_rsi_all_gains_is_100():
    assert rsi([1, 2, 3, 4, 5], 3)[-1] == pytest.approx(100.0)


def test_macd_histogram_is_line_minus_signal():
    closes = [100 + i * 0.5 for i in range(60)]
    line, signal, hist = macd(closes)
    assert line[24] is None and line[25] is not None
    assert signal[32] is None and signal[33] is not None
    assert hist[-1] == pytest.approx(line[-1] - signal[-1])
    assert line[-1] > 0   # uptrend: fast EMA above slow EMA


def test_atr_wilder_reference():
    candles = [
        bar(0, 10, 11, 9, 10),
        bar(1, 10, 12, 10, 11),   # TR 2
        bar(2, 11, 11.5, 10, 11), # TR 1.5
        bar(3, 11, 14, 11, 13),   # TR 3
    ]
    out = atr(candles, 2)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(1.75)
    assert out[3] == pytest.approx((1.75 + 3) / 2)


def test_adx_strong_in_steady_trend():
    out = adx(trend(60), 14)
    assert out[26] is None and out[27] is not None   # needs 2*period bars
    assert out[-1] > 40


def test_supertrend_up_in_uptrend_and_flips_down():
    candles = trend(30) + trend(30, step=-2.0, start=131.0)
    line, is_up = supertrend(candles, 10, 3)
    assert line[9] is None and is_up[9] is None
    assert is_up[29] is True and line[29] < candles[29].close
    assert is_up[-1] is False and line[-1] > candles[-1].close


def test_roc():
    out = roc([100, 110, 121], 1)
    assert out[0] is None
    assert out[1:] == [pytest.approx(10.0), pytest.approx(10.0)]


def test_vwap_cumulative_typical_price_resets_each_session():
    day1 = [bar(0, 10, 12, 8, 10, 100), bar(1, 10, 15, 9, 12, 300)]
    day2 = [bar(0, 50, 51, 49, 50, 10, start=T0 + timedelta(days=1))]
    out = vwap(day1 + day2)
    assert out[0] == pytest.approx(10.0)
    assert out[1] == pytest.approx((10 * 100 + 12 * 300) / 400)
    assert out[2] == pytest.approx(50.0)   # new session resets


def test_rvol_time_of_day_uses_cumulative_curve():
    curve = [100.0 * (i + 1) for i in range(375)]
    today = [bar(0, 1, 1, 1, 1, 150), bar(1, 1, 1, 1, 1, 150)]
    assert rvol_time_of_day(today, curve) == pytest.approx(300 / 200)


def test_rvol_time_of_day_none_without_curve_or_bars():
    assert rvol_time_of_day([], [1.0] * 375) is None
    assert rvol_time_of_day([bar(0, 1, 1, 1, 1)], []) is None
