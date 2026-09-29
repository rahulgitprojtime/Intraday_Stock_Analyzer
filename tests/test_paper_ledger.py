from datetime import date, datetime, time

import pytest

from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.ledger import Ledger
from src.paper.orders import Bar, Bracket, Side
from src.utils.config import load_yaml

COSTS = CostModel.from_dict(load_yaml("costs.yaml"))
CFG = BrokerConfig(100_000, 25_000, 3, 0, time(15, 15))
T = lambda h, m: datetime(2026, 9, 25, h, m)  # noqa: E731


def run(ledger, run_id="r1"):
    ledger.start_run(run_id, "BACKTEST", "TEST", 100_000, {"k": 1})
    b = BacktestBroker(CFG, COSTS, ledger, run_id)
    b.on_tick(Bar("AAA", T(10, 15), 99, 100, 98, 99.5))
    b.place_order("AAA", Side.BUY, 10, bracket=Bracket(5, 7.5), tag="ENTRY")
    b.on_tick(Bar("AAA", T(10, 16), 100, 101, 99.5, 100.5))
    b.on_tick(Bar("AAA", T(10, 17), 101, 108, 100.8, 107))
    ledger.record_equity(run_id, T(10, 18), b.equity())
    ledger.record_day(run_id, date(2026, 9, 25), b.trades, b.equity())
    ledger.commit()
    return b


def test_orders_fills_positions_trades_and_daily_pnl_are_persisted(tmp_path):
    path = tmp_path / "ledger.sqlite"
    b = run(Ledger(path))
    db = Ledger(path)                                   # fresh connection reads it back
    [r] = db.runs()
    assert (r["run_id"], r["mode"], r["strategy"], r["params"]) == ("r1", "BACKTEST", "TEST",
                                                                   {"k": 1})
    orders = db.orders("r1")
    assert [(o["tag"], o["status"]) for o in orders] == [
        ("ENTRY", "FILLED"), ("STOP_LOSS", "CANCELLED"), ("TARGET", "FILLED")]
    fills = db.fills("r1")
    assert [f["tag"] for f in fills] == ["ENTRY", "TARGET"]
    assert fills[1]["stt"] > 0 and fills[0]["stamp_duty"] > 0
    assert sum(f["total_charges"] for f in fills) == pytest.approx(b.trades[0]["charges"])
    [p] = db.positions("r1")
    assert p["quantity"] == 0 and p["realized_pnl"] == pytest.approx(75.0)
    [t] = db.trades("r1")
    assert t["net_pnl"] == pytest.approx(b.trades[0]["net_pnl"])
    [d] = db.daily_pnl("r1")
    assert d["trades"] == 1 and d["net_pnl"] == pytest.approx(t["net_pnl"])
    assert db.equity_curve("r1") == [(T(10, 18).isoformat(), pytest.approx(b.equity()))]


def test_restarting_a_run_replaces_its_rows_only(tmp_path):
    db = Ledger(tmp_path / "l.sqlite")
    run(db, "a")
    run(db, "b")
    run(db, "a")                                        # rerun of the same backtest id
    assert len(db.trades("a")) == 1 and len(db.trades("b")) == 1
    assert sorted(r["run_id"] for r in db.runs()) == ["a", "b"]
