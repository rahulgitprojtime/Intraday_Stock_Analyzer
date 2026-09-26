from datetime import datetime, timedelta

from src.data.models import Candle, Exchange, Instrument, Segment
from src.indicators.core import ema
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.setups import (
    SetupState as S,
    ema_pullback,
    gap_and_go,
    momentum_burst,
    narrow_cpr_trend,
    opening_range_breakout,
    pdh_breakout,
    relative_strength,
    vwap_pullback,
    vwap_reclaim,
)

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)
T0 = datetime(2026, 9, 25, 9, 15)


def bar(i, o, h, l, c, v=100, complete=True):
    return Candle(INST, 1, T0 + timedelta(minutes=i), o, h, l, c, v, complete)


def doji(i, c, v=100):
    return bar(i, c, c, c, c, v)


def closes_after(start, closes):
    """Bars from index `start` with the given closes (range +-0.2)."""
    return [bar(start + j, c, c + 0.2, c - 0.2, c) for j, c in enumerate(closes)]


def prep(**kw):
    base = dict(prev_high=105.0, prev_low=95.0, prev_close=100.0, pivot=100.0,
                cpr_top=100.1, cpr_bottom=99.9, cpr_width_pct=0.2,
                is_nr7=None, is_inside_day=None, atr=4.0, atr_pct=4.0)
    base.update(kw)
    return DailyPrep(**base)


# --- ORB -------------------------------------------------------------------
OR5 = [bar(i, 100, 101, 99, 100) for i in range(5)]


def orb(closes, ext=2.0):
    return opening_range_breakout(OR5 + closes_after(5, closes), 5, ext).state


def test_orb_states():
    assert orb([100.8]) == S.FORMING
    assert orb([99.2]) == S.NONE
    assert orb([101.5]) == S.TRIGGERED
    assert orb([101.5, 104.0]) == S.EXTENDED
    assert orb([101.5, 100.5]) == S.FAILED


def test_orb_needs_complete_range_and_ignores_forming_bar():
    assert opening_range_breakout(OR5[:3], 5, 2.0).state == S.NONE
    forming = bar(5, 100, 102, 100, 101.8, complete=False)
    assert opening_range_breakout(OR5 + [forming], 5, 2.0).state == S.NONE


# --- PDH -------------------------------------------------------------------
def test_pdh_breakout_triggers_on_cross():
    today = [bar(0, 103, 104, 103, 104)] + closes_after(1, [105.5])
    assert pdh_breakout(today, prep(), 2.0).state == S.TRIGGERED


def test_pdh_opened_above_is_not_a_breakout():
    today = [bar(0, 106, 107, 106, 106.5)] + closes_after(1, [107])
    assert pdh_breakout(today, prep(), 2.0).state == S.NONE


# --- VWAP reclaim / pullback ----------------------------------------------
def test_vwap_reclaim_states():
    base = [doji(i, c) for i, c in enumerate([100, 100, 100, 99, 99])]
    assert vwap_reclaim(base, 2.0).state == S.FORMING
    assert vwap_reclaim(base + [doji(5, 100.5)], 2.0).state == S.TRIGGERED
    assert vwap_reclaim(base + [doji(5, 100.5), doji(6, 99)], 2.0).state == S.FAILED


def test_vwap_reclaim_stale_cross_is_none():
    closes = [100, 99] + [101] * 12
    assert vwap_reclaim([doji(i, c) for i, c in enumerate(closes)], 5.0).state == S.NONE


VW = [bar(0, 100, 100, 100, 100, 100_000)] + [
    bar(i, c - 1, c + 0.2, c - 1.2, c, 1) for i, c in [(1, 101), (2, 102), (3, 103)]
]
TOUCH = bar(4, 101, 101.2, 100.05, 100.8, 1)


def test_vwap_pullback_states():
    assert vwap_pullback(VW + [TOUCH], 2.0).state == S.FORMING
    assert vwap_pullback(VW + [TOUCH] + closes_after(5, [101.5]), 2.0).state == S.TRIGGERED
    assert vwap_pullback(VW + [TOUCH] + closes_after(5, [99.5]), 2.0).state == S.FAILED
    assert vwap_pullback(VW, 2.0).state == S.NONE


# --- EMA pullback ----------------------------------------------------------
def test_ema_pullback_touch_then_bounce():
    up = closes_after(0, [100 + 0.5 * i for i in range(30)])
    c = 113.5
    e9 = ema([b.close for b in up] + [c], 9)[-1]
    touch = bar(30, 114, 114.2, e9 - 0.01, c)
    assert ema_pullback(up, 2.0).state == S.NONE
    assert ema_pullback(up + [touch], 2.0).state == S.FORMING
    assert ema_pullback(up + [touch] + closes_after(31, [114.5]), 2.0).state == S.TRIGGERED


# --- Narrow CPR ------------------------------------------------------------
def test_narrow_cpr_trend_states():
    p = prep()
    assert narrow_cpr_trend(closes_after(0, [100.5]), p, 2.0).state == S.TRIGGERED
    assert narrow_cpr_trend(closes_after(0, [103.0]), p, 2.0).state == S.EXTENDED
    assert narrow_cpr_trend(closes_after(0, [100.0]), p, 2.0).state == S.FORMING
    assert narrow_cpr_trend(closes_after(0, [100.5, 100.0]), p, 2.0).state == S.FAILED
    wide = prep(cpr_width_pct=0.8)
    assert narrow_cpr_trend(closes_after(0, [100.5]), wide, 2.0).state == S.NONE


# --- Gap and go ------------------------------------------------------------
GAP = bar(0, 102, 103, 101.8, 102.8)


def test_gap_and_go_states():
    assert gap_and_go([GAP], prep(), 2.0).state == S.FORMING
    assert gap_and_go([GAP] + closes_after(1, [103.5]), prep(), 2.0).state == S.TRIGGERED
    assert gap_and_go([GAP] + closes_after(1, [99.5]), prep(), 2.0).state == S.FAILED
    small = bar(0, 100.5, 101, 100.4, 100.9)
    assert gap_and_go([small] + closes_after(1, [101.5]), prep(), 2.0).state == S.NONE


# --- Relative strength vs NIFTY -------------------------------------------
def test_relative_strength():
    idx = [doji(0, 1000), doji(1, 1005)]
    assert relative_strength([doji(0, 100), doji(1, 102)], idx).state == S.TRIGGERED
    flat = [doji(0, 1000), doji(1, 1000)]
    assert relative_strength([doji(0, 100), doji(1, 100.3)], flat).state == S.FORMING
    assert relative_strength([doji(0, 100), doji(1, 99)], flat).state == S.NONE


# --- 1-min momentum burst --------------------------------------------------
QUIET = [bar(i, 100, 100.15, 99.95, 100.1) for i in range(20)]


def test_momentum_burst_needs_body_and_volume():
    burst = bar(20, 100, 100.55, 99.95, 100.5, 300)
    assert momentum_burst(QUIET + [burst]).state == S.TRIGGERED
    weak = bar(20, 100, 100.55, 99.95, 100.5, 150)
    assert momentum_burst(QUIET + [weak]).state == S.NONE


def test_momentum_burst_fails_below_midpoint():
    burst = bar(20, 100, 100.55, 99.95, 100.5, 300)
    after = closes_after(21, [100.4, 100.1])
    assert momentum_burst(QUIET + [burst] + after).state == S.FAILED
