from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.context import nifty_context
from src.recommendation.data_quality import assess_data_quality

NIFTY = Instrument("NIFTY", Exchange.NSE, Segment.CASH, is_index=True)
T0 = datetime(2026, 9, 25, 9, 15)


def bar(i, o, c, complete=True, inst=NIFTY):
    return Candle(inst, 1, T0 + timedelta(minutes=i), o, max(o, c), min(o, c), c, 0, complete)


def at(minute):
    return T0 + timedelta(minutes=minute)


# --- market context ---------------------------------------------------------
def test_nifty_ramp():
    ctx = nifty_context([bar(0, 1000, 1001), bar(1, 1001, 1002)], at(2), 120)
    assert ctx.status == "available" and ctx.source == "NIFTY"
    assert ctx.nifty_change_pct == pytest.approx(0.2)
    assert ctx.score == pytest.approx(70)
    assert nifty_context([bar(0, 1000, 990)], at(1), 120).score == 0
    assert nifty_context([bar(0, 1000, 1010)], at(1), 120).score == 100


def test_nifty_missing_or_stale_is_unavailable_not_neutral():
    empty = nifty_context([], at(1), 120)
    assert empty.status == "unavailable" and empty.score is None
    stale = nifty_context([bar(0, 1000, 1001)], at(10), 120)
    assert stale.status == "unavailable" and stale.score is None and "stale" in stale.reason


def test_nifty_ignores_forming_bar():
    ctx = nifty_context([bar(0, 1000, 1001), bar(1, 1001, 1050, complete=False)], at(1), 120)
    assert ctx.nifty_change_pct == pytest.approx(0.1)


# --- data quality -----------------------------------------------------------
def q(bars, minute, **kw):
    flags = dict(has_prep=True, has_volume_curve=True, has_index=True) | kw
    return assess_data_quality(bars, at(minute), 120, **flags)


def test_fresh_data_ok():
    dq = q([bar(0, 1, 1), bar(1, 1, 1)], 2)
    assert dq.status == "OK" and not dq.stale and dq.data_age_seconds == 0
    assert dq.market_data_timestamp == at(1).isoformat() and dq.missing_inputs == ()


def test_stale_candles():
    dq = q([bar(0, 1, 1)], 5)
    assert dq.status == "STALE" and dq.stale and dq.data_age_seconds == 240


def test_missing_critical_inputs_incomplete():
    assert q([], 2).status == "INCOMPLETE"
    dq = q([bar(0, 1, 1)], 1, has_prep=False)
    assert dq.status == "INCOMPLETE" and "prep" in dq.missing_inputs


def test_missing_noncritical_inputs_listed_but_ok():
    dq = q([bar(0, 1, 1)], 1, has_volume_curve=False, has_index=False)
    assert dq.status == "OK" and dq.missing_inputs == ("volume_curve", "index")


def test_forming_bar_ignored():
    dq = q([bar(0, 1, 1), bar(1, 1, 1, complete=False)], 1)
    assert dq.market_data_timestamp == at(0).isoformat()
