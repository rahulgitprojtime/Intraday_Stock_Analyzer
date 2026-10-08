"""Shared entry/exit rules of the directional strategies — DECISIONS #33. SIMULATION ONLY."""

from datetime import date, datetime, time, timedelta

import pytest

from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.orders import Bar
from src.paper.session import TradingSession
from src.paper.strategies.common import DirectionalStrategy, EntryRules, lower_highs
from src.paper.strategies.scalp import ScalpConfig, ScalpPriceAction
from src.paper.strategies.trend import IntradayTrend, TrendConfig
from src.utils.config import load_yaml

RAW = load_yaml("costs.yaml")
REAL = CostModel.from_dict(RAW)
FREE = CostModel.from_dict(RAW | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
CFG = BrokerConfig(100_000, 49_000, 2, 0, time(15, 15), allow_short=True)
DAY = date(2026, 10, 9)
OPEN = datetime.combine(DAY, time(9, 15))


class FixedSignal(DirectionalStrategy):
    """Signals on every bar from `start` bars on, with a fixed stop distance."""
    name = entry_tag = "FIXED"

    def __init__(self, risk=0.5, start=12, rules=EntryRules(), risk_per_trade=1000.0,
                 max_trades_per_symbol=1, max_hold_minutes=None):
        super().__init__(risk_per_trade, 49_000, 2.0, max_trades_per_symbol, time(9, 20),
                         time(15, 0), max_hold_minutes, rules=rules)
        self.risk, self.start = risk, start

    def signal(self, sym, bars, k, ctx, direction):
        return ("FIXED", bars[-1].close, self.risk) if len(bars) >= self.start else None


def bars_from(ohlc, sym="AAA", start=OPEN):
    return [Bar(sym, start + timedelta(minutes=i), *x, 1000) for i, x in enumerate(ohlc)]


def rising(n=40, sym="AAA", p=101.0, step=0.05, wiggle=0.1):
    return bars_from([(p + i * step, p + i * step + wiggle, p + i * step - wiggle,
                       p + i * step + step) for i in range(n)], sym)


def run(strat, series, bias="BULLISH", prev=100.0, costs=FREE, nifty_pct=None, broker_cfg=CFG):
    session = TradingSession(strat, BacktestBroker(broker_cfg, costs))
    session.start_day(DAY)
    sent = {"bias": bias}
    if nifty_pct is not None:
        sent["nifty_change_pct"] = nifty_pct
    session.ctx.meta = {"sentiment": sent}
    if isinstance(series, list):
        series = {series[0].symbol: series}
    for s in series:
        session.ctx.prev_close[s] = prev
    for i in range(len(next(iter(series.values())))):
        step = [b[i] for b in series.values()]
        session.step(step[0].ts + timedelta(minutes=1), step)
    session.end_day(DAY)
    return session.broker


def placed(strat):
    return [s for s in strat.signals if s["status"] != "SKIPPED"]


# -- 1. stop noise floor -----------------------------------------------------------------

def test_stop_is_widened_to_a_percent_floor():
    strat = FixedSignal(risk=0.01, rules=EntryRules(min_stop_pct=0.15))
    run(strat, rising())
    sig = placed(strat)[0]
    entry_ref = rising()[11].close
    assert sig["risk"] == pytest.approx(entry_ref * 0.0015, rel=1e-6)


def test_stop_is_widened_to_the_1min_atr():
    strat = FixedSignal(risk=0.01, rules=EntryRules(stop_atr_mult=1.0))
    run(strat, rising(wiggle=0.4))
    assert placed(strat)[0]["risk"] == pytest.approx(0.8, abs=1e-6)      # every bar spans 0.8


def test_a_structure_stop_already_wider_than_the_floor_is_kept():
    strat = FixedSignal(risk=2.0, rules=EntryRules(stop_atr_mult=1.0, min_stop_pct=0.15))
    run(strat, rising())
    assert placed(strat)[0]["risk"] == 2.0


# -- 2. cost gate and trade cap ------------------------------------------------------------

def test_cost_gate_skips_a_trade_whose_target_cannot_pay_the_charges():
    small = FixedSignal(risk=0.2, risk_per_trade=8,     # 40 shares: 2R = Rs 16, charges ~Rs 13
                        rules=EntryRules(min_reward_cost_mult=3.0))
    broker = run(small, rising(), costs=REAL)
    assert placed(small) == [] and broker.orders() == []
    assert small.signals[0]["reason"].startswith("target")
    big = FixedSignal(risk=0.5, rules=EntryRules(min_reward_cost_mult=3.0))   # 2R = Rs 2,000
    run(big, rising(), costs=REAL)
    assert len(placed(big)) == 1


def test_daily_trade_cap_stops_new_entries():
    strat = FixedSignal(rules=EntryRules(max_trades_per_day=1))
    run(strat, {s: rising(sym=s) for s in ("AAA", "BBB")})
    assert len(placed(strat)) == 1


# -- 3. relative strength and structure ------------------------------------------------------

def test_long_needs_to_beat_nifty_and_short_needs_to_lag_it():
    strat = FixedSignal(rules=EntryRules(rs_filter=True))
    run(strat, rising(), nifty_pct=5.0)                     # stock ~+1.6%, NIFTY +5%
    assert placed(strat) == [] and "NIFTY" in strat.signals[0]["reason"]
    strat = FixedSignal(rules=EntryRules(rs_filter=True))
    run(strat, rising(), nifty_pct=0.5)
    assert len(placed(strat)) == 1
    falling = [Bar(b.symbol, b.ts, 200 - b.open, 200 - b.low, 200 - b.high, 200 - b.close,
                   b.volume) for b in rising()]             # mirror around 100: down ~1.6%
    strat = FixedSignal(rules=EntryRules(rs_filter=True))
    run(strat, falling, bias="BEARISH", nifty_pct=-0.5)
    assert len(placed(strat)) == 1                          # lags NIFTY: short allowed
    strat = FixedSignal(rules=EntryRules(rs_filter=True))
    run(strat, falling, bias="BEARISH", nifty_pct=-5.0)
    assert placed(strat) == []                              # beats NIFTY: no short


def test_rs_filter_is_off_when_nifty_move_is_unknown():
    strat = FixedSignal(rules=EntryRules(rs_filter=True))
    run(strat, rising())
    assert len(placed(strat)) == 1


def peaks(highs):
    """Bars whose highs trace `highs` (lows 0.3 below)."""
    return bars_from([(h - 0.1, h, h - 0.3, h - 0.05) for h in highs])


def test_lower_highs_detects_falling_swing_highs():
    assert lower_highs(peaks([100, 101, 102, 101, 100, 100.5, 101.5, 100.5, 100, 99.8]))
    assert not lower_highs(peaks([100, 101, 102, 101, 100, 101, 103, 102, 101, 100.8]))
    assert not lower_highs(peaks([100, 100.1, 100.2]))                   # no swings yet


def test_no_short_on_higher_lows_and_no_long_on_lower_highs():
    up = peaks([101, 102, 103, 102, 101, 101.5, 102.5, 101.5, 101, 100.8,
                101, 101.2, 101.3, 101.4])                                # lower highs
    strat = FixedSignal(start=14, rules=EntryRules(structure_filter=True))
    run(strat, up)
    assert placed(strat) == [] and "lower highs" in strat.signals[0]["reason"]
    # the real chart of a short making higher lows is the mirrored chart making lower highs
    down = [Bar(b.symbol, b.ts, 200 - b.open, 200 - b.low, 200 - b.high, 200 - b.close, 1000)
            for b in up]
    strat = FixedSignal(start=14, rules=EntryRules(structure_filter=True))
    run(strat, down, bias="BEARISH")
    assert placed(strat) == []


# -- 4. slot release ---------------------------------------------------------------------------

def test_stalled_position_is_closed_to_free_the_slot():
    strat = FixedSignal(risk=1.0, rules=EntryRules(stall_minutes=30, stall_r=0.5))
    flat = rising(12) + bars_from([(101.6, 101.7, 101.5, 101.6)] * 50,
                                  start=OPEN + timedelta(minutes=12))
    broker = run(strat, flat)
    [t] = broker.trades
    assert t["exit_tag"] == "STALL_EXIT" and 30 <= t["holding_minutes"] <= 32


def test_a_position_that_reached_half_r_is_not_stalled():
    strat = FixedSignal(risk=1.0, rules=EntryRules(stall_minutes=30, stall_r=0.5))
    pop = rising(12) + bars_from([(101.6, 102.3, 101.5, 101.8)]
                                 + [(101.8, 101.9, 101.7, 101.8)] * 50,
                                 start=OPEN + timedelta(minutes=12))
    broker = run(strat, pop)
    [t] = broker.trades
    assert t["exit_tag"] != "STALL_EXIT"


# -- 5. configuration ---------------------------------------------------------------------------

def test_paper_yaml_turns_the_rules_on_for_both_strategies():
    raw = load_yaml("paper.yaml")
    sc, tr = ScalpConfig.from_dict(raw["scalp"]), TrendConfig.from_dict(raw["trend"])
    assert (sc.target_r, sc.risk_per_trade, sc.max_position_value) == (2.0, 1000, 49_000)
    assert (sc.max_trades_per_symbol, sc.max_trades_per_day, sc.min_reward_cost_mult) == (1, 20, 3)
    assert (sc.stop_atr_mult, sc.min_stop_pct, sc.rs_filter, sc.structure_filter) == \
        (1.0, 0.15, True, True)
    assert (tr.max_hold_minutes, tr.stall_minutes, tr.stall_r) == (120, 30, 0.5)
    assert (tr.risk_per_trade, tr.max_position_value, tr.rs_filter) == (1000, 49_000, True)
    b = raw["broker"]
    assert (b["max_open_positions"], b["max_position_value"]) == (2, 49_000)
    assert ScalpPriceAction(sc).rules.min_reward_cost_mult == 3
    assert IntradayTrend(tr).max_hold_minutes == 120
