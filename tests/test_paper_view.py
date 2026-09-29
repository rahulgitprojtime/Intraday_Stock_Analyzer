from datetime import datetime, time
from pathlib import Path

import pytest

from app.paper_view import live_view, open_ledger, run_options, run_report
from src.paper.broker import BrokerConfig, PaperBroker
from src.paper.costs import CostModel
from src.paper.ledger import Ledger
from src.paper.orders import Bar, Side
from src.utils.config import load_yaml
from tests.test_paper_backtest import DAY, orb, session_bars

COSTS = CostModel.from_dict(load_yaml("costs.yaml"))
CFG = BrokerConfig(100_000, 25_000, 3, 0, time(15, 15))


def backtest_db(tmp_path):
    from src.paper.backtest import run_backtest
    db = Ledger(tmp_path / "bt.sqlite")
    res = run_backtest(orb(), [(DAY, session_bars(special={(10, 0): (102, 107, 101.9, 106)}))],
                       CFG, COSTS, ledger=db, run_id="bt1")
    return db, res


def paper_db(tmp_path):
    db = Ledger(tmp_path / "paper.sqlite")
    db.start_run("p1", "PAPER", "ORB", 100_000)
    b = PaperBroker(CFG, COSTS, db, "p1")
    b.on_tick(Bar.tick("AAA", datetime(2026, 9, 25, 10, 0), 100.0))
    b.place_order("AAA", Side.BUY, 10)
    b.on_tick(Bar.tick("AAA", datetime(2026, 9, 25, 10, 0, 5), 100.0))
    b.on_tick(Bar.tick("AAA", datetime(2026, 9, 25, 10, 1), 103.0))
    db.record_equity("p1", datetime(2026, 9, 25, 10, 1), b.equity())
    b.persist_positions(datetime(2026, 9, 25, 10, 1))      # what the session does each minute
    db.commit()
    return db, b


def test_missing_ledger_is_none(tmp_path):
    assert open_ledger(tmp_path / "nope.sqlite") is None


def test_backtest_report_matches_the_run(tmp_path):
    db, res = backtest_db(tmp_path)
    [run] = run_options(db)
    rep = run_report(db, run)
    assert rep["metrics"]["net_pnl"] == pytest.approx(res.metrics["net_pnl"])
    assert rep["metrics"]["trades"] == 1 and len(rep["daily"]) == 1
    assert rep["equity"] and set(rep["equity"][0]) == {"at", "equity"}


def test_live_view_shows_positions_with_unrealized_pnl(tmp_path):
    db, b = paper_db(tmp_path)
    [run] = run_options(db, "PAPER")
    v = live_view(db, run)
    [p] = v["positions"]
    assert (p["symbol"], p["quantity"], p["unrealized_pnl"]) == ("AAA", 10, 30.0)
    assert v["unrealized"] == 30.0 and v["charges"] > 0
    assert v["equity"] == pytest.approx(b.equity())


def test_page_renders_headless(tmp_path, monkeypatch):
    testing = pytest.importorskip("streamlit.testing.v1")
    backtest_db(tmp_path)
    paper_db(tmp_path)
    monkeypatch.setenv("PAPER_LEDGER", str(tmp_path / "paper.sqlite"))
    monkeypatch.setenv("BACKTEST_LEDGER", str(tmp_path / "bt.sqlite"))
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "1_Paper_trading.py"
    at = testing.AppTest.from_file(str(page), default_timeout=30).run()
    assert not at.exception
    assert any("SIMULATION ONLY" in w.value for w in at.warning)
    assert not at.button                                   # display only: no order controls
