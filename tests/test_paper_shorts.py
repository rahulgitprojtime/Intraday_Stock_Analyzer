"""Short positions, mirrored brackets and restart resume — DECISIONS #30. SIMULATION ONLY."""

from datetime import date, datetime, time

import pytest

from src.paper.backtest import run_backtest
from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.day_report import render, summarize
from src.paper.ledger import Ledger
from src.paper.orders import Bar, Bracket, OrderStatus, OrderType, Side
from src.paper.strategies.orb import OpeningRangeBreakout
from src.utils.config import load_yaml
from tests.test_paper_backtest import session_bars

RAW = load_yaml("costs.yaml")
COSTS = CostModel.from_dict(RAW)
FREE = CostModel.from_dict(RAW | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
CFG = BrokerConfig(100_000, 25_000, 3, 0, time(15, 15), allow_short=True)
DAY = date(2026, 9, 25)


def T(h, m):
    return datetime.combine(DAY, time(h, m))


def bar(h, m, o, hi, lo, c, sym="AAA"):
    return Bar(sym, T(h, m), o, hi, lo, c)


def broker(cfg=CFG, costs=FREE, ledger=None, run_id="run"):
    b = BacktestBroker(cfg, costs, ledger, run_id)
    b.on_tick(bar(10, 15, 100, 100, 100, 100))
    return b


def shorted(b, qty=10, bracket=Bracket(2, 3)):
    o = b.place_order("AAA", Side.SELL, qty, tag="ENTRY", bracket=bracket)
    assert o.status is OrderStatus.OPEN, o.reason
    [f] = b.on_tick(bar(10, 16, 100, 100.2, 99.8, 100))
    return f


def test_short_entry_mirrors_the_bracket_stop_above_target_below():
    b = broker()
    shorted(b)
    assert b.positions()["AAA"].quantity == -10
    exits = {o.tag: o for o in b.open_orders("AAA")}
    assert (exits["STOP_LOSS"].side, exits["STOP_LOSS"].trigger_price) == (Side.BUY, 102)
    assert (exits["TARGET"].side, exits["TARGET"].limit_price) == (Side.BUY, 97)
    [t] = [t for t in b.open_trades()]
    assert (t["direction"], t["stop_loss"], t["target"]) == ("SHORT", 102, 97)


def test_short_target_profit_is_entry_minus_exit():
    b = broker()
    shorted(b)
    [f] = b.on_tick(bar(10, 20, 98, 98, 96.5, 96.8))
    assert (f.tag, f.side, f.price) == ("TARGET", Side.BUY, 97)
    [t] = b.trades
    assert (t["direction"], t["gross_pnl"], t["net_pnl"]) == ("SHORT", 30, 30)
    assert (t["stop_loss"], t["target"]) == (102, 97)
    assert b.positions() == {} and b.open_orders() == []
    assert b.cash == pytest.approx(100_030)


def test_short_stop_loss_and_gap_fill():
    b = broker()
    shorted(b)
    [f] = b.on_tick(bar(10, 20, 103, 104, 102.5, 103.5))       # gaps above the stop
    assert (f.tag, f.price) == ("STOP_LOSS", 103)
    assert b.trades[0]["gross_pnl"] == pytest.approx(-30)


def test_square_off_buys_back_shorts_and_costs_are_charged():
    b = broker(costs=COSTS)
    f = shorted(b, bracket=Bracket(5, 5))
    assert f.charges.stt > 0 and f.charges.stamp_duty == 0     # STT on the sell (entry) leg
    b.on_tick(bar(10, 30, 99, 99, 99, 99))
    [x] = b.square_off(T(15, 15))
    assert (x.side, x.tag, x.price) == (Side.BUY, "SQUARE_OFF", 99)
    assert x.charges.stamp_duty > 0 and x.charges.stt == 0
    [t] = b.trades
    assert t["gross_pnl"] == pytest.approx(10)
    assert t["net_pnl"] == pytest.approx(10 - f.charges.total - x.charges.total)
    assert b.equity() == pytest.approx(b.cash)


def test_short_needs_allow_short():
    o = broker(cfg=BrokerConfig(100_000, 25_000, 3, 0, time(15, 15))).place_order(
        "AAA", Side.SELL, 5)
    assert o.status is OrderStatus.REJECTED and "long-only" in o.reason


def test_an_exit_cannot_flip_a_position():
    b = broker()
    shorted(b, bracket=None)
    o = b.place_order("AAA", Side.BUY, 15)
    assert o.status is OrderStatus.REJECTED and "no flipping" in o.reason


def test_a_short_blocks_its_notional_as_margin():
    b = broker(cfg=BrokerConfig(30_000, 25_000, 3, 0, time(15, 15), allow_short=True))
    shorted(b, qty=200, bracket=None)                            # Rs 20,000 short
    b.on_tick(bar(10, 17, 100, 100, 100, 100, sym="BBB"))
    o = b.place_order("BBB", Side.BUY, 150)                      # Rs 15,000 > 10,000 left
    assert o.status is OrderStatus.REJECTED and "capital" in o.reason
    assert b.place_order("BBB", Side.BUY, 90).status is OrderStatus.OPEN


def test_orb_shorts_the_breakdown_with_stop_near_the_range_high():
    special = {(9, 30): (99.2, 99.3, 98.4, 98.5), (9, 31): (98.4, 98.5, 98.2, 98.3),
               (10, 0): (98, 98, 92, 93)}
    orb = OpeningRangeBreakout(range_minutes=15, target_r=2.0, risk_per_trade=500,
                               allow_short=True)
    res = run_backtest(orb, [(DAY, session_bars(special=special, after=98.0))], CFG, FREE)
    [t] = res.trades
    assert (t["direction"], t["entry_at"], t["entry_price"]) == ("SHORT", T(9, 31).isoformat(),
                                                                 98.4)
    assert t["quantity"] == 200                                  # 500 / (101 - 98.5)
    assert (t["stop_loss"], t["target"]) == (100.9, 93.4)
    assert (t["exit_tag"], t["exit_price"]) == ("TARGET", 93.4)
    assert t["gross_pnl"] == pytest.approx(200 * 5.0)


def test_orb_without_allow_short_ignores_breakdowns():
    special = {(9, 30): (99.2, 99.3, 98.4, 98.5), (9, 31): (98.4, 98.5, 98.2, 98.3)}
    res = run_backtest(OpeningRangeBreakout(), [(DAY, session_bars(special=special, after=98.0))],
                       CFG, FREE)
    assert res.trades == []


def test_resume_rebuilds_positions_cash_and_rearms_stop_and_target(tmp_path):
    db = Ledger(tmp_path / "p.sqlite")
    db.start_run("r", "BACKTEST", "X", 100_000)
    b = broker(costs=COSTS, ledger=db, run_id="r")
    shorted(b)                                                   # AAA short 10, stop 102 tgt 97
    b.on_tick(bar(10, 17, 50, 50, 50, 50, sym="BBB"))
    b.place_order("BBB", Side.BUY, 20, tag="ENTRY", bracket=Bracket(1, 2))
    b.on_tick(bar(10, 18, 50, 50, 50, 50, sym="BBB"))            # BBB long 20, stop 49 tgt 52
    b.on_tick(bar(10, 19, 52.5, 52.5, 52.1, 52.2, sym="BBB"))    # BBB target: closed trade
    b.on_tick(bar(10, 20, 50, 50, 50, 50, sym="CCC"))
    pending = b.place_order("CCC", Side.BUY, 5, tag="ENTRY")     # never filled before the crash
    db.commit()
    cash, equity = b.cash, b.equity()

    r = BacktestBroker(CFG, COSTS, db, "r")                      # the restarted worker
    held = r.resume(db.orders("r"), db.fills("r"), db.trades("r"))
    assert held == ["AAA"]
    assert r.positions()["AAA"].quantity == -10 and r.cash == pytest.approx(cash)
    assert r.equity() == pytest.approx(equity)
    assert [t["symbol"] for t in r.trades] == ["BBB"]
    assert {o.tag: (o.side, o.trigger_price or o.limit_price) for o in r.open_orders()} == {
        "STOP_LOSS": (Side.BUY, 102), "TARGET": (Side.BUY, 97)}
    assert {o["id"]: o["status"] for o in db.orders("r")}[pending.id] == "CANCELLED"
    new = r.place_order("AAA", Side.BUY, 1)
    assert int(new.id[1:]) > int(pending.id[1:])                 # ids never collide
    r.cancel_order(new.id)
    [f] = r.on_tick(bar(10, 40, 103, 103, 103, 103))             # the stop still works
    assert (f.tag, f.price) == ("STOP_LOSS", 103)
    trades = {t["symbol"]: t for t in db.trades("r")}
    assert (trades["AAA"]["direction"], trades["AAA"]["stop_loss"], trades["AAA"]["target"]) == \
        ("SHORT", 102, 97)
    assert trades["AAA"]["net_pnl"] < trades["AAA"]["gross_pnl"] == pytest.approx(-30)


def test_ledger_adds_new_trade_columns_to_an_old_file(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trades (run_id TEXT, symbol TEXT, entry_at TEXT, exit_at TEXT, "
                "quantity INTEGER, entry_price REAL, exit_price REAL, gross_pnl REAL, "
                "charges REAL, net_pnl REAL, entry_tag TEXT, exit_tag TEXT, "
                "holding_minutes INTEGER)")
    con.commit()
    con.close()
    db = Ledger(path)
    db.add_trade("r", {"symbol": "A", "entry_at": "x", "exit_at": "y", "quantity": 1,
                       "entry_price": 1, "exit_price": 2, "gross_pnl": 1, "charges": 0,
                       "net_pnl": 1, "entry_tag": "E", "exit_tag": "TARGET",
                       "holding_minutes": 1, "direction": "SHORT", "stop_loss": 3,
                       "target": 0.5})
    assert db.trades("r")[0]["direction"] == "SHORT"


def test_day_report_lists_each_trade_with_stop_target_and_net(tmp_path):
    db = Ledger(tmp_path / "p.sqlite")
    run = f"paper:{DAY.isoformat()}:orb"
    db.start_run(run, "PAPER", "ORB", 100_000)
    b = broker(ledger=db, run_id=run)
    shorted(b)
    b.on_tick(bar(10, 20, 98, 98, 96.5, 96.8))
    db.record_day(run, DAY, b.trades, b.equity())
    db.commit()
    text = render(db, DAY)
    assert "| AAA | SHORT | 10 | 10:16 | 100.00 | 102.00 | 97.00 | 10:20 | 97.00 | target |" in text
    assert "**net ₹30.00**" in text and "SIMULATION ONLY" in text
    assert summarize(b.trades)["short"] == 1
    assert "No paper runs" in render(db, date(2026, 9, 26))
