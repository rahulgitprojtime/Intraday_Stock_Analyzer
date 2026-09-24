from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.quantitative.daily_prep import (
    SESSION_MINUTES,
    avg_cumulative_volume_curve,
    compute_daily_prep,
    wilder_atr,
)

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)


def day(i, o, h, l, c, v=1000):
    return Candle(INST, 1440, datetime(2026, 9, 1) + timedelta(days=i), o, h, l, c, v)


def test_cpr_prior_levels_and_width():
    days = [day(0, 100, 110, 90, 105)]
    prep = compute_daily_prep(days)
    assert (prep.prev_high, prep.prev_low, prep.prev_close) == (110, 90, 105)
    assert prep.pivot == pytest.approx(305 / 3)
    assert prep.cpr_bottom == pytest.approx(100.0)          # (H+L)/2
    assert prep.cpr_top == pytest.approx(2 * 305 / 3 - 100)  # 2P - BC
    width = (prep.cpr_top - prep.cpr_bottom) / prep.pivot * 100
    assert prep.cpr_width_pct == pytest.approx(width)
    assert prep.is_nr7 is None and prep.atr is None  # not enough history


def test_cpr_top_is_always_above_bottom():
    # close near the low puts TC below BC; we normalize to top >= bottom
    prep = compute_daily_prep([day(0, 108, 110, 90, 91)])
    assert prep.cpr_top >= prep.cpr_bottom


def test_nr7_and_inside_day():
    ranges = [20, 18, 16, 15, 14, 12]
    days = [day(i, 100, 100 + r / 2, 100 - r / 2, 100) for i, r in enumerate(ranges)]
    days.append(day(6, 100, 104, 96, 100))  # range 8: narrowest, inside prior day
    prep = compute_daily_prep(days)
    assert prep.is_nr7 is True
    assert prep.is_inside_day is True

    days[-1] = day(6, 100, 130, 96, 100)  # wide + breaks prior high
    prep = compute_daily_prep(days)
    assert prep.is_nr7 is False
    assert prep.is_inside_day is False


def test_wilder_atr_reference():
    # constant true range of 10 -> ATR 10
    days = [day(i, 100, 105, 95, 100) for i in range(20)]
    assert wilder_atr(days, 14) == pytest.approx(10.0)
    assert wilder_atr(days[:14], 14) is None  # needs period + 1 candles

    # gap day: TR uses prior close. TRs after the seed: 10 x13 then 30
    days = [day(i, 100, 105, 95, 100) for i in range(15)]
    days.append(day(15, 125, 130, 120, 125))  # TR = 130 - 100 = 30
    # seed = mean of first 14 TRs (all 10); then (10*13 + 30) / 14
    assert wilder_atr(days, 14) == pytest.approx((10 * 13 + 30) / 14)


def test_atr_pct_uses_prev_close():
    days = [day(i, 100, 105, 95, 100) for i in range(20)]
    prep = compute_daily_prep(days)
    assert prep.atr == pytest.approx(10.0)
    assert prep.atr_pct == pytest.approx(10.0)


def test_empty_daily_raises():
    with pytest.raises(ValueError):
        compute_daily_prep([])


def minute(d, m, v):
    ts = datetime(2026, 9, d, 9, 15) + timedelta(minutes=m)
    return Candle(INST, 1, ts, 100, 100, 100, 100, v)


def test_avg_cumulative_volume_curve_forward_fills_and_averages():
    candles = [minute(1, 0, 100), minute(1, 2, 50),   # minute 1 missing
               minute(2, 0, 300), minute(2, 1, 100), minute(2, 2, 100)]
    curve = avg_cumulative_volume_curve(candles)
    assert len(curve) == SESSION_MINUTES
    assert curve[0] == pytest.approx(200)   # (100 + 300) / 2
    assert curve[1] == pytest.approx(250)   # (100 + 400) / 2 — day 1 ffilled
    assert curve[2] == pytest.approx(325)   # (150 + 500) / 2
    assert curve[-1] == pytest.approx(325)


def test_curve_ignores_out_of_session_candles_and_empty_input():
    pre = Candle(INST, 1, datetime(2026, 9, 1, 9, 0), 1, 1, 1, 1, 999)
    assert avg_cumulative_volume_curve([pre, minute(1, 0, 10)])[0] == pytest.approx(10)
    assert avg_cumulative_volume_curve([]) == []
