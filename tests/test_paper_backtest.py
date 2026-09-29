from datetime import date, datetime, time, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.paper.backtest import ensure_cached, load_day, run_backtest, run_day
from src.paper.broker import BacktestBroker, BrokerConfig, PaperBroker
from src.paper.costs import CostModel
from src.paper.ledger import Ledger
from src.paper.metrics import NA
from src.paper.orders import Bar
from src.paper.performance import max_drawdown, performance, sharpe
from src.paper.session import TradingSession
from src.paper.strategies.orb import OpeningRangeBreakout
from src.paper.strategy import Strategy
from src.storage.candle_cache import IntradayCandleCache
from src.utils.config import load_yaml

RAW_COSTS = load_yaml("costs.yaml")
COSTS = CostModel.from_dict(RAW_COSTS)
FREE = CostModel.from_dict(RAW_COSTS | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
CFG = BrokerConfig(100_000, 25_000, 3, 0, time(15, 15))
DAY = date(2026, 9, 25)


def at(h, m, d=DAY):
    return datetime.combine(d, time(h, m))


def session_bars(sym="AAA", d=DAY, special=None, after=102.0):
    """Range 99-101 for 09:15-09:29, a breakout close at 09:30, flat after."""
    special = special or {}
    bars, t = [], at(9, 15, d)
    while t.time() < time(15, 30):
        hm = (t.hour, t.minute)
        if hm in special:
            o, h, l, c = special[hm]
        elif t.time() < time(9, 30):
            o, h, l, c = 100, 101, 99, 100
        elif hm == (9, 30):
            o, h, l, c = 100.8, 101.6, 100.7, 101.5
        elif hm == (9, 31):
            o, h, l, c = 101.6, 102, 101.4, 101.9
        else:
            o, h, l, c = after, after + 0.2, after - 0.2, after
        bars.append(Bar(sym, t, o, h, l, c, 1000))
        t += timedelta(minutes=1)
    return bars


def orb():
    return OpeningRangeBreakout(range_minutes=15, target_r=2.0, risk_per_trade=500)


def test_orb_enters_at_the_bar_after_the_breakout_close_and_hits_target():
    res = run_backtest(orb(), [(DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)}))],
                       CFG, FREE)
    [t] = res.trades
    assert (t["entry_at"], t["entry_price"]) == (at(9, 31).isoformat(), 101.6)   # not 09:30
    assert t["quantity"] == 200                         # 500 / (101.5 - 99)
    assert (t["exit_tag"], t["exit_price"]) == ("TARGET", 106.6)             # fill + 2 x 2.5
    assert t["gross_pnl"] == pytest.approx(200 * 5.0)


def test_orb_stop_sits_near_the_range_low():
    res = run_backtest(orb(), [(DAY, session_bars(special={(10, 0): (101, 101, 98, 98.5)}))],
                       CFG, FREE)
    [t] = res.trades
    assert (t["exit_tag"], t["exit_price"]) == ("STOP_LOSS", 99.1)          # 101.6 - 2.5


def test_open_position_is_squared_off_at_1515_at_the_last_close():
    res = run_backtest(orb(), [(DAY, session_bars())], CFG, FREE)
    [t] = res.trades
    assert (t["exit_tag"], t["exit_at"], t["exit_price"]) == \
        ("SQUARE_OFF", at(15, 15).isoformat(), 102.0)


def test_no_trade_when_range_is_too_wide_or_no_breakout():
    wide = session_bars(special={(9, 20): (100, 110, 99, 100)})
    flat = session_bars(special={(9, 30): (100, 100.5, 99.5, 100), (9, 31): (100, 100.5, 99.5, 100)},
                        after=100.0)
    assert run_backtest(orb(), [(DAY, wide)], CFG, FREE).trades == []
    assert run_backtest(orb(), [(DAY, flat)], CFG, FREE).trades == []


class Spy(Strategy):
    name = "SPY"

    def __init__(self):
        self.violations = []

    def on_bars(self, as_of, bars, ctx):
        for sym in ctx.symbols():
            last = ctx.bars(sym)[-1]
            if last.ts + timedelta(minutes=1) > as_of:
                self.violations.append((as_of, last.ts))


def test_strategy_only_ever_sees_closed_bars():
    spy = Spy()
    run_backtest(spy, [(DAY, session_bars() + session_bars("BBB"))], CFG, FREE)
    assert spy.violations == []


def test_future_bars_cannot_change_earlier_fills():
    clean = session_bars(special={(10, 0): (102, 107, 101.9, 106)})
    poisoned = [b if b.ts < at(9, 32) else
                Bar(b.symbol, b.ts, b.open * 10, b.high * 10, b.low * 10, b.close * 10)
                for b in clean]
    a = run_backtest(orb(), [(DAY, clean)], CFG, FREE)
    b = run_backtest(orb(), [(DAY, poisoned)], CFG, FREE)
    entry = lambda r: [(o.symbol, o.tag, o.fill_price, o.filled_at)  # noqa: E731
                       for o in r.orders if o.tag == "ORB_ENTRY"]
    assert entry(a) == entry(b) == [("AAA", "ORB_ENTRY", 101.6, at(9, 31))]


def test_costs_are_charged_and_reported():
    res = run_backtest(orb(), [(DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)}))],
                       CFG, COSTS)
    [t] = res.trades
    assert t["charges"] > 0 and t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["charges"])
    m = res.metrics
    assert m["net_pnl"] == pytest.approx(t["net_pnl"], abs=0.01)
    assert m["end_equity"] == pytest.approx(100_000 + t["net_pnl"], abs=0.01)
    assert m["win_rate"] == 100.0 and m["sharpe"] == NA           # one day: not computable


def test_multi_day_run_writes_the_ledger(tmp_path):
    d2 = DAY + timedelta(days=3)
    days = [(DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)})),
            (d2, session_bars(d=d2, special={(10, 0): (101, 101, 98, 98.5)}))]
    db = Ledger(tmp_path / "bt.sqlite")
    res = run_backtest(orb(), days, CFG, COSTS, ledger=db, run_id="orb-test")
    assert [d["trades"] for d in res.daily] == [1, 1]
    assert [t["exit_tag"] for t in db.trades("orb-test")] == ["TARGET", "STOP_LOSS"]
    assert len(db.daily_pnl("orb-test")) == 2 and db.equity_curve("orb-test")
    assert isinstance(res.metrics["sharpe"], float)
    assert res.metrics["max_drawdown"] > 0


# -- performance ------------------------------------------------------------------------

def test_max_drawdown_and_sharpe():
    assert max_drawdown([110, 100, 120, 90, 130], start=100) == (30.0, 25.0)
    assert sharpe([101, 102], start=100) != NA
    assert sharpe([101], start=100) == NA and sharpe([100, 100], start=100) == NA


def test_performance_counts_every_trade():
    trades = [{"net_pnl": 10, "gross_pnl": 12, "charges": 2},
              {"net_pnl": -5, "gross_pnl": -3, "charges": 2},
              {"net_pnl": 0.0, "gross_pnl": 2, "charges": 2}]
    m = performance(trades, [(None, 100_010), (None, 100_005)],
                    [{"end_equity": 100_005}], 100_000)
    assert (m["trades"], m["winners"], m["losers"]) == (3, 1, 1)
    assert m["win_rate"] == pytest.approx(33.33) and m["profit_factor"] == 2.0
    assert m["net_pnl"] == 5 and m["charges"] == 6 and m["max_drawdown"] == 5


# -- caching ----------------------------------------------------------------------------

class FakeAdapter:
    def __init__(self, candles):
        self.candles, self.calls = candles, 0

    def get_historical_candles(self, req):
        self.calls += 1
        return [c for c in self.candles if req.start_time <= c.timestamp <= req.end_time]


def test_ensure_cached_downloads_once_and_marks_holidays(tmp_path):
    inst = Instrument("AAA", Exchange.NSE, Segment.CASH)
    d1, d2 = date(2026, 9, 24), date(2026, 9, 25)
    candles = [Candle(inst, 1, at(9, 15, d1), 1, 1, 1, 1, 10)]      # d2: no data (holiday)
    cache, fake = IntradayCandleCache(tmp_path), FakeAdapter(candles)
    assert ensure_cached(cache, fake, inst, [d1, d2], today=date(2026, 9, 29)) == 2
    assert ensure_cached(cache, fake, inst, [d1, d2], today=date(2026, 9, 29)) == 0
    assert fake.calls == 1
    assert len(load_day(cache, ["AAA"], d1)) == 1 and load_day(cache, ["AAA"], d2) == []


def test_ensure_cached_never_caches_today(tmp_path):
    inst = Instrument("AAA", Exchange.NSE, Segment.CASH)
    fake = FakeAdapter([])
    assert ensure_cached(IntradayCandleCache(tmp_path), fake, inst, [DAY], today=DAY) == 0


# -- same strategy, paper mode ------------------------------------------------------------

def test_orb_runs_unchanged_on_the_paper_broker_with_tick_fills():
    broker = PaperBroker(CFG, FREE)
    s = TradingSession(orb(), broker, fill_on_bars=False)
    s.start_day(DAY)
    bars = session_bars()
    for b in bars[:16]:                                   # through the 09:30 breakout bar
        broker.on_tick(Bar.tick(b.symbol, b.ts, b.close))  # live price before the bar closes
        s.step(b.ts + timedelta(minutes=1), [b])
    assert broker.positions() == {} and len(broker.open_orders()) == 1
    [f] = s.on_tick(Bar.tick("AAA", at(9, 31).replace(second=3), 101.7))
    assert (f.price, f.tag) == (101.7, "ORB_ENTRY")
    assert broker.positions()["AAA"].quantity == 200


def test_backtest_and_session_share_one_code_path():
    """run_day is what run_backtest uses; driving it by hand gives the same trades."""
    broker = BacktestBroker(CFG, FREE)
    s = TradingSession(orb(), broker)
    run_day(s, DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)}))
    ref = run_backtest(orb(), [(DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)}))],
                       CFG, FREE)
    assert broker.trades == ref.trades
