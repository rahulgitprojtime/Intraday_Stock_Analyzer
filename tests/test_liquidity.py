from datetime import date, datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.data.prep_builder import build_prep
from src.quantitative.liquidity import (
    LiquidityHistory,
    evaluate_liquidity,
    liquidity_history,
    liquidity_score,
)

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)
FILTERS = {"min_price": 20.0, "min_avg_daily_volume": 500_000, "min_avg_traded_value": 5e7}


def bar(ts, c, v, complete=True):
    return Candle(INST, 1, ts, c, c, c, c, v, complete)


def test_history_averages_per_session():
    d1, d2 = datetime(2026, 9, 23, 9, 15), datetime(2026, 9, 24, 9, 15)
    h = liquidity_history([bar(d1, 100, 10), bar(d1 + timedelta(minutes=1), 110, 10),
                           bar(d2, 100, 40)])
    assert (h.avg_daily_volume, h.avg_traded_value, h.sessions) == (30, 3050, 2)


def test_history_empty_is_none():
    assert liquidity_history([]) is None


def test_score_log_ramp():
    assert liquidity_score(5e7, 5e7) == pytest.approx(0)
    assert liquidity_score(5e8, 5e7) == pytest.approx(100)
    assert liquidity_score(5e7 * 10 ** 0.5, 5e7) == pytest.approx(50)
    assert liquidity_score(5e9, 5e7) == 100
    assert liquidity_score(None, 5e7) is None
    assert liquidity_score(1e8, None) is None


def test_eligible_liquid_stock():
    today = [bar(datetime(2026, 9, 25, 9, 15), 500, 1000),
             bar(datetime(2026, 9, 25, 9, 16), 500, 1000, complete=False)]
    liq = evaluate_liquidity(LiquidityHistory(1e6, 1e8, 20), today, FILTERS)
    assert liq.eligible is True
    assert liq.score == pytest.approx(30.103, abs=0.01)
    assert liq.current_traded_value == 500_000      # complete bars only
    assert liq.spread_pct is None                   # needs depth (M7)


def test_ineligible_reasons():
    ts = datetime(2026, 9, 25, 9, 15)
    low_vol = evaluate_liquidity(LiquidityHistory(1e5, 1e8, 20), [bar(ts, 500, 1)], FILTERS)
    assert low_vol.eligible is False and "volume" in low_vol.reason
    cheap = evaluate_liquidity(LiquidityHistory(1e6, 1e8, 20), [bar(ts, 10, 1)], FILTERS)
    assert cheap.eligible is False and "price" in cheap.reason


def test_missing_history_is_unavailable_not_invented():
    liq = evaluate_liquidity(None, [], FILTERS)
    assert liq.eligible is None and liq.score is None and liq.avg_traded_value is None


class _HistoryAdapter:
    def __init__(self, candles):
        self.candles = candles

    def get_historical_candles(self, request):
        return [c for c in self.candles if request.start_time <= c.timestamp <= request.end_time]


def test_build_prep_carries_liquidity_history():
    candles = []
    for d in (date(2026, 9, 23), date(2026, 9, 24)):
        start = datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15)
        candles += [Candle(INST, 1, start + timedelta(minutes=i), 100, 101, 99, 100, 10)
                    for i in range(3)]
    result = build_prep(_HistoryAdapter(candles), INST, date(2026, 9, 25))
    assert result.liquidity.sessions == 2
    assert result.liquidity.avg_daily_volume == 30
