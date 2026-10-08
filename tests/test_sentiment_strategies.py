"""Market sentiment, scalp price action and intraday trend — DECISIONS #31. SIMULATION ONLY."""

from datetime import date, datetime, time, timedelta

import pytest

from src.market.sentiment import (
    SentimentConfig,
    directions,
    market_sentiment,
    trade_direction,
)
from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.orders import Bar, Side
from src.paper.session import TradingSession
from src.paper.strategies.common import mirror_bar
from src.paper.strategies.scalp import ScalpConfig, ScalpPriceAction, higher_low, tight_breakout
from src.paper.strategies.trend import IntradayTrend, TrendConfig, indicators_agree
from src.paper.strategies.common import to_candles
from src.utils.config import load_yaml

RAW = load_yaml("costs.yaml")
FREE = CostModel.from_dict(RAW | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
CFG = BrokerConfig(100_000, 25_000, 3, 0, time(15, 15), allow_short=True)
DAY = date(2026, 10, 9)
OPEN = datetime.combine(DAY, time(9, 15))


def ramp(start, step, n):
    return [start + step * i for i in range(n)]


# -- sentiment ------------------------------------------------------------------------

def test_both_indices_up_is_bullish_and_down_is_bearish():
    up = {"NIFTY": (ramp(100, 0.02, 40), 100.0, 99.5),
          "BANKNIFTY": (ramp(200, 0.05, 40), 200.0, 199.0)}
    s = market_sentiment(up)
    assert (s["bias"], s["score"]) == ("BULLISH", 6)
    assert s["nifty_change_pct"] == pytest.approx((100.78 / 99.5 - 1) * 100, abs=1e-3)
    down = {k: ([2 * v[2] - c for c in v[0]], v[1], v[2]) for k, v in
            {"NIFTY": (ramp(100, 0.02, 40), 99.5, 99.5),
             "BANKNIFTY": (ramp(200, 0.05, 40), 199.0, 199.0)}.items()}
    assert market_sentiment(down)["bias"] == "BEARISH"


def test_indices_disagreeing_or_flat_is_neutral():
    mixed = {"NIFTY": (ramp(100, 0.02, 40), 100.0, 99.5),
             "BANKNIFTY": (ramp(200, -0.05, 40), 200.0, 201.0)}
    assert market_sentiment(mixed)["bias"] == "NEUTRAL"
    flat = {"NIFTY": ([100.0] * 40, 100.0, 100.0), "BANKNIFTY": ([200.0] * 40, 200.0, 200.0)}
    assert market_sentiment(flat)["bias"] == "NEUTRAL"
    assert market_sentiment({})["bias"] == "NEUTRAL"


def test_bias_maps_to_scan_sides_and_trade_side():
    assert directions("BULLISH") == ("LONG",) and directions("BEARISH") == ("SHORT",)
    assert directions("NEUTRAL") == ("LONG", "SHORT")
    assert [trade_direction(b) for b in ("BULLISH", "BEARISH", "NEUTRAL")] == \
        ["LONG", "SHORT", None]


def test_sentiment_config_reads_universe_yaml():
    c = SentimentConfig.from_dict(load_yaml("universe.yaml")["scan"]["sentiment"])
    assert (c.dead_band_pct, c.trend_minutes, c.min_index_score, c.min_total_score) == \
        (0.1, 15, 1, 3)


# -- price action rules ------------------------------------------------------------------

def bars_from(ohlc, sym="AAA", start=OPEN):
    return [Bar(sym, start + timedelta(minutes=i), *x, 1000) for i, x in enumerate(ohlc)]


def base_then_breakout():
    """Drift up 09:15-09:28, a tight 6-bar base, then a strong breakout bar."""
    ohlc = []
    p = 100.0
    for _ in range(14):
        ohlc.append((p, p + 0.3, p - 0.1, p + 0.2))
        p += 0.2
    for _ in range(6):
        ohlc.append((p, p + 0.12, p - 0.1, p + 0.02))
    ohlc.append((p + 0.05, p + 0.75, p + 0.02, p + 0.7))                # breakout
    return bars_from(ohlc)


def test_tight_breakout_and_its_stop_below_the_base():
    bars = base_then_breakout()
    stop = tight_breakout(bars, ScalpConfig())
    base_low = min(b.low for b in bars[-7:-1])
    assert stop is not None and stop < base_low
    weak = bars[:-1] + [Bar("AAA", bars[-1].ts, 102.85, 103.4, 102.5, 102.9)]   # long wick
    assert tight_breakout(weak, ScalpConfig()) is None


def test_higher_low_continuation():
    seq = [100, 100.4, 100.9, 101.3, 100.9, 100.6, 100.8, 101.2, 101.7, 102.0,
           101.6, 101.3, 101.2]
    ohlc = [(c - 0.1, c + 0.15, c - 0.15, c) for c in seq]
    ohlc.append((101.2, 101.85, 101.15, 101.8))                       # breaks prior bar high
    bars = bars_from(ohlc)
    stop = higher_low(bars, ScalpConfig(min_bars=8))
    assert stop is not None and stop < 101.05                          # below the pullback low


def test_mirror_turns_a_breakdown_into_a_breakout():
    up = base_then_breakout()
    k = 100.0
    down = [mirror_bar(b, k) for b in up]                              # the real falling chart
    back = [mirror_bar(b, k) for b in down]
    assert [(b.open, b.high, b.low, b.close) for b in back] == \
        [pytest.approx((b.open, b.high, b.low, b.close)) for b in up]
    assert down[-1].close < min(b.low for b in down[-7:-1])            # breaks the base low


# -- scalp strategy end to end -----------------------------------------------------------------

def run_scalp(bars, bias, after=None, prev_close=100.0):
    strat = ScalpPriceAction(ScalpConfig())
    session = TradingSession(strat, BacktestBroker(CFG, FREE))
    session.start_day(DAY)
    session.ctx.meta = {"sentiment": {"bias": bias}}
    session.ctx.prev_close["AAA"] = prev_close
    for b in bars + (after or []):
        session.step(b.ts + timedelta(minutes=1), [b])
    session.end_day(DAY)
    return strat, session.broker


def test_scalp_goes_long_on_a_bullish_breakout_with_stop_and_target():
    bars = base_then_breakout()
    p = bars[-1].close
    after = bars_from([(p, p + 3, p - 0.05, p + 2.9)], start=bars[-1].ts + timedelta(minutes=1))
    strat, broker = run_scalp(bars, "BULLISH", after)
    [sig] = strat.signals
    assert (sig["direction"], sig["setup"]) == ("LONG", "TIGHT_BREAKOUT")
    [t] = broker.trades
    assert (t["direction"], t["exit_tag"]) == ("LONG", "TARGET")
    assert t["target"] - t["entry_price"] == pytest.approx(1.5 * (t["entry_price"] - t["stop_loss"]))


def test_scalp_shorts_the_mirror_setup_in_a_bearish_market():
    k = 100.0
    down = [mirror_bar(b, k) for b in base_then_breakout()]
    p = down[-1].close
    after = bars_from([(p, p + 0.05, p - 3, p - 2.9)], start=down[-1].ts + timedelta(minutes=1))
    strat, broker = run_scalp(down, "BEARISH", after)
    [sig] = strat.signals
    assert (sig["direction"], sig["setup"]) == ("SHORT", "TIGHT_BREAKOUT")
    [t] = broker.trades
    assert t["direction"] == "SHORT" and t["stop_loss"] > t["entry_price"] > t["target"]
    assert (t["exit_tag"], t["gross_pnl"] > 0) == ("TARGET", True)


def test_scalp_stands_aside_against_or_without_market_direction():
    bars = base_then_breakout()
    for bias in ("NEUTRAL", "BEARISH"):
        strat, broker = run_scalp(bars, bias)
        assert strat.signals == [] and broker.trades == []


def test_scalp_never_trades_without_a_setup():
    chop = bars_from([(100 + (i % 3) * 0.3, 100.5 + (i % 3) * 0.3, 99.8 + (i % 3) * 0.3,
                       100.1 + (i % 3) * 0.3) for i in range(120)])
    strat, broker = run_scalp(chop, "BULLISH")
    assert strat.signals == [] and broker.trades == []


def test_scalp_time_exit_after_15_minutes():
    bars = base_then_breakout()
    p = bars[-1].close
    flat = bars_from([(p + 0.05, p + 0.1, p, p + 0.05)] * 20,
                     start=bars[-1].ts + timedelta(minutes=1))
    _, broker = run_scalp(bars, "BULLISH", flat)
    [t] = broker.trades
    assert t["exit_tag"] == "TIME_EXIT" and 15 <= t["holding_minutes"] <= 17


# -- intraday trend ------------------------------------------------------------------------------

def trend_day(n=120, sym="AAA"):
    """An uptrend with regular pullbacks (6 bars up, 4 bars down)."""
    ohlc, p = [], 100.0
    for i in range(n):
        step = -0.07 if i % 10 >= 6 else 0.08
        o, c = p, p + step
        ohlc.append((o, max(o, c) + 0.05, min(o, c) - 0.05, c))
        p = c
    return bars_from(ohlc, sym)


def test_indicators_agree_on_a_clean_uptrend_and_not_while_warming_up():
    c1 = to_candles(trend_day())
    assert indicators_agree(c1, TrendConfig()) is None
    assert indicators_agree(c1[:10], TrendConfig()).startswith("warming up")
    falling = to_candles([mirror_bar(b, 100.0) for b in trend_day()])
    assert indicators_agree(falling, TrendConfig()) is not None


def run_trend(bars, bias):
    strat = IntradayTrend(TrendConfig())
    session = TradingSession(strat, BacktestBroker(CFG, FREE))
    session.start_day(DAY)
    session.ctx.meta = {"sentiment": {"bias": bias}}
    session.ctx.prev_close["AAA"] = 100.0
    for b in bars:
        session.step(b.ts + timedelta(minutes=1), [b])
    session.end_day(DAY)
    return strat, session.broker


def test_trend_enters_only_on_a_fresh_trigger_at_a_5min_close():
    strat, broker = run_trend(trend_day(), "BULLISH")
    assert strat.signals, "a clean uptrend should give at least one confirmed setup"
    for s in strat.signals:
        t = datetime.fromisoformat(s["at"])
        assert (t - OPEN).total_seconds() / 60 % 5 == 0               # 5-min bar boundaries
        assert s["direction"] == "LONG" and s["setup"] in TrendConfig().setups
    assert len({(s["symbol"], s["setup"]) for s in strat.signals}) == len(strat.signals)
    assert all(t["direction"] == "LONG" for t in broker.trades)


def test_trend_shorts_the_mirrored_downtrend_and_stands_aside_when_neutral():
    down = [mirror_bar(b, 100.0) for b in trend_day()]
    strat, broker = run_trend(down, "BEARISH")
    assert strat.signals and all(s["direction"] == "SHORT" for s in strat.signals)
    strat, broker = run_trend(trend_day(), "NEUTRAL")
    assert strat.signals == [] and broker.trades == []


def test_backtest_bias_from_index_bars_when_no_worker_sentiment():
    strat = ScalpPriceAction(ScalpConfig())
    session = TradingSession(strat, BacktestBroker(CFG, FREE))
    session.start_day(DAY)
    session.ctx.prev_close.update({"AAA": 100.0, "NIFTY": 99.0, "BANKNIFTY": 198.0})
    bars = base_then_breakout()
    nifty = bars_from([(100 + i * 0.02,) * 4 for i in range(len(bars))], "NIFTY")
    bank = bars_from([(200 + i * 0.05,) * 4 for i in range(len(bars))], "BANKNIFTY")
    for b, n, bk in zip(bars, nifty, bank):
        session.step(b.ts + timedelta(minutes=1), [b, n, bk])
    assert [s["direction"] for s in strat.signals] == ["LONG"]
    assert all(o.symbol == "AAA" for o in session.broker.orders())   # indices never traded
