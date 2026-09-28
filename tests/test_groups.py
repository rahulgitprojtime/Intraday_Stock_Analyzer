from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.context import MarketContext
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.groups import (
    liquidity_group,
    market_group,
    momentum_group,
    news_group,
    price_group,
    sector_group,
    setup_group,
    volume_group,
)
from src.quantitative.liquidity import Liquidity
from src.quantitative.microstructure import SymbolFeed

T0 = datetime(2026, 9, 25, 9, 15)
PREP = DailyPrep(prev_high=101, prev_low=99, prev_close=100, pivot=100, cpr_top=100.1,
                 cpr_bottom=99.9, cpr_width_pct=0.2, is_nr7=None, is_inside_day=None,
                 atr=2.0, atr_pct=2.0)


def bars(closes, vols=None, sym="AAA", index=False):
    inst = Instrument(sym, Exchange.NSE, Segment.CASH, is_index=index)
    vols = vols or [1000] * len(closes)
    out, prev = [], closes[0]
    for i, (c, v) in enumerate(zip(closes, vols)):
        o = prev
        out.append(Candle(inst, 1, T0 + timedelta(minutes=i), o, max(o, c) + 0.01,
                          min(o, c) - 0.01, c, v))
        prev = c
    return out


RISING = [100 + 0.05 * i for i in range(60)]          # steady trend: 100 -> 102.95
FALLING = [100 - 0.05 * i for i in range(60)]


def zigzag(step_up, step_down, n=60, start=100.0):
    out, p = [], start
    for i in range(n):
        p += step_up if i % 2 == 0 else -step_down
        out.append(round(p, 4))
    return out


NOISY_UP = zigzag(0.10, 0.04)          # net up, RSI ~70 (realistic, not 100)
NOISY_DOWN = zigzag(-0.10, -0.04)      # mirror: net down


def test_price_group_rewards_strength_and_structure():
    up = price_group(bars(RISING), PREP, range_expansion=1.0)
    down = price_group(bars(FALLING), PREP, range_expansion=1.0)
    assert up.value > 80 and down.value <= 20
    assert up.parts["structure"] == 100                  # above previous-day high (101)
    assert up.parts["change"] == pytest.approx((102.95 / 100 - 1) * 100 / 3 * 100)
    assert down.parts["change"] == 0 and down.parts["vwap"] == 0


def test_volume_group_rvol_and_acceleration():
    accel = [1000] * 55 + [3000] * 5                     # last 5 bars 3x the prior pace
    g = volume_group(rvol=2.5, bars=bars(RISING, accel))
    assert g.parts["rvol"] == pytest.approx(50)          # ramp 1 -> 4
    assert g.parts["acceleration"] == pytest.approx(100)
    assert volume_group(rvol=None, bars=bars(RISING[:10])).value is None   # nothing to score


def test_momentum_group_trend_vs_downtrend():
    up = momentum_group(bars(NOISY_UP), roc_full_pct=1.0)
    down = momentum_group(bars(NOISY_DOWN), roc_full_pct=1.0)
    assert up.value > 50 and down.value < 10
    assert up.parts["ema_structure"] == 100 and down.parts["ema_structure"] == 0
    assert up.parts["rsi"] > 90


def test_adx_only_counts_in_an_uptrend():
    """Long-only: ADX is direction-agnostic, so a strong DOWNtrend must not
    earn momentum points."""
    down = momentum_group(bars(NOISY_DOWN), roc_full_pct=1.0)
    assert down.parts["adx"] == 0
    assert momentum_group(bars(RISING[:3]), 1.0).value is None


def test_momentum_overextended_rsi_is_not_rewarded():
    spike = [100] * 40 + [100 + 0.6 * i for i in range(20)]     # RSI near 100
    g = momentum_group(bars(spike), roc_full_pct=1.0)
    assert g.parts["rsi"] == 50


def test_setup_group_includes_confluence_capped():
    assert setup_group(60.0, 3.0).value == 63.0
    assert setup_group(100.0, 5.0).value == 100.0


def test_market_group_regime_and_bank_index():
    nifty_up = bars([25000 + 5 * i for i in range(40)], [0] * 40, "NIFTY", True)
    m = MarketContext("available", 70.0, 0.4)
    g = market_group(m, nifty_up, bank=None)
    assert g.parts == {"nifty": 70.0, "regime": 100.0, "bank_nifty": None}
    b = market_group(m, nifty_up, bank=MarketContext("available", 20.0, -0.5))
    assert b.parts["bank_nifty"] == 20.0 and b.value < g.value
    assert market_group(MarketContext("unavailable", None, None), [], None).value is None


def test_sector_group_available_and_missing():
    blk = {"status": "available", "sector_score": 80.0, "stock_vs_sector": 0.25}
    assert sector_group(blk).parts == {"sector": 80.0, "stock_vs_sector": 75.0}
    assert sector_group({"status": "unavailable", "sector_score": None,
                         "stock_vs_sector": None}).value is None


def test_liquidity_group_spread_and_micro_only_in_scalp():
    liq = Liquidity(True, 1e6, 1e9, 1e8, 0.05, 90.0, "ok")
    feed = SymbolFeed(0.05, 0.3, 1.5, 70.0, 1.0, 1.0)
    scalp = liquidity_group(liq, feed, "SCALP")
    day = liquidity_group(liq, feed, "DAY")
    assert scalp.parts == {"liquidity": 90.0, "spread": 100.0, "microstructure": 70.0}
    assert day.parts["microstructure"] is None


@pytest.mark.parametrize("verdict,value", [
    ("POSITIVE", 100), ("NEUTRAL", 50), ("NO_RELEVANT_INFORMATION", 50), ("MIXED", 40),
    ("NEGATIVE", 0), ("UNAVAILABLE", None), ("PENDING", None)])
def test_news_group(verdict, value):
    assert news_group({"verdict": verdict}).value == value
    assert news_group(None).value is None
