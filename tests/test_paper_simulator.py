"""Recommendation strategy on the simulated broker (M10 behaviour, DECISIONS #20/#29)."""

from datetime import datetime, time

import pytest

from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.journal import Journal
from src.paper.orders import Bar
from src.paper.policy import PaperConfig
from src.paper.session import TradingSession
from src.paper.strategies.recommendation import RecommendationStrategy
from src.utils.config import load_yaml

CFG = PaperConfig.from_dict(load_yaml("paper.yaml"))
RAW_COSTS = load_yaml("costs.yaml")
COSTS = CostModel.from_dict(RAW_COSTS)
FREE = CostModel.from_dict(RAW_COSTS | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
DAY = datetime(2026, 9, 25)
T = lambda h, m: DAY.replace(hour=h, minute=m)  # noqa: E731
VERSIONS = {"strategy_version": "v1", "config_hash": "abc", "git_commit": "deadbee"}


def bar(sym, h, m, o, hi, lo, c):
    return Bar(sym, T(h, m), o, hi, lo, c, 1000)


def rec(sym="AAA", rank=1, **kw):
    return {"symbol": sym, "mode": "DAY", "category": "CANDIDATE", "score": 70.0, "rank": rank,
            "eligible_for_top_n": True, "profile": "IN_PLAY_STRONG_SETUP",
            "setup": {"best": "ORB15", "best_state": "TRIGGERED",
                      "confluence_families": ["price_structure"]},
            "quantitative": {"is_in_play": True, "in_play_score": 60.0, "microstructure": None},
            "data_quality": {"status": "OK"}, "market_context": {"score": 55.0},
            "sector_context": {"verdict": "CONFIRMED"}, "qualitative": {"status": "unavailable"},
            "prerequisites": [], "prerequisites_summary": "Checked: technicals ✓"} | kw


class Harness:
    def __init__(self, tmp_path, recs=None, atr=None, costs=FREE, slippage=0, capital=100_000):
        self.recs = recs or {}
        self.journal = Journal(tmp_path / "j.jsonl")
        self.strategy = RecommendationStrategy(CFG, lambda t: self.recs.get(t, []), atr or {},
                                               self.journal, "run1", VERSIONS, "replay")
        self.broker = BacktestBroker(BrokerConfig(capital, 25_000, 3, slippage, time(15, 15)),
                                     costs)
        self.session = TradingSession(self.strategy, self.broker)
        self.session.start_day(DAY.date())

    def step(self, h, m, *bars):
        self.session.step(T(h, m), bars)

    def trade(self):
        [t] = self.journal.trades()
        return t


def entered(tmp_path, **kw):
    h = Harness(tmp_path, {T(10, 16): [rec()]}, {"AAA": 20.0}, **kw)   # risk 5: stop 95, tgt 107.5
    h.step(10, 16, bar("AAA", 10, 15, 99, 100, 98, 99.5))
    assert h.journal.trades() == []                        # signal only; no same-bar fill
    h.step(10, 17, bar("AAA", 10, 16, 100, 101, 99.5, 100.5))
    return h


def test_fill_at_next_bar_open_then_target(tmp_path):
    h = entered(tmp_path)
    t = h.trade()
    assert (t["entry_price"], t["entry_timestamp"]) == (100.0, "2026-09-25T10:16:00")
    assert (t["stop_loss"], t["target"], t["stop_method"]) == (95.0, 107.5, "atr")
    assert t["signal_at"] == "2026-09-25T10:16:00" and t["exit_reason"] is None
    h.step(10, 18, bar("AAA", 10, 17, 101, 108, 100.8, 107))
    t = h.trade()
    assert (t["exit_reason"], t["exit_price"]) == ("TARGET", 107.5)
    assert t["gross_pnl"] == pytest.approx(75.0) and t["risk_multiple"] == pytest.approx(1.5)
    assert t["holding_minutes"] == 1 and t["pnl_percent"] == pytest.approx(7.5)


def exit_on(tmp_path, exit_bar, **kw):
    h = entered(tmp_path, **kw)
    h.step(10, 18, exit_bar)
    return h.trade()


def test_stop_loss(tmp_path):
    t = exit_on(tmp_path, bar("AAA", 10, 17, 99, 99.5, 94, 95))
    assert (t["exit_reason"], t["exit_price"], t["risk_multiple"]) == ("STOP_LOSS", 95.0, -1.0)


def test_same_candle_ambiguity_counts_as_stop(tmp_path):
    t = exit_on(tmp_path, bar("AAA", 10, 17, 100, 110, 90, 105))
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == 95.0
    assert t["exit_note"] == "stop and target touched in one bar: stop assumed first"


def test_gap_through_stop_fills_at_the_worse_open(tmp_path):
    t = exit_on(tmp_path, bar("AAA", 10, 17, 92, 93, 91, 92.5))
    assert (t["exit_reason"], t["exit_price"]) == ("STOP_LOSS", 92.0)


def test_square_off_at_1515_at_the_last_closed_bar(tmp_path):
    h = entered(tmp_path)
    h.step(15, 15, bar("AAA", 15, 14, 101, 102, 100, 101.2))   # data gap until 15:14 (Groww)
    t = h.trade()
    assert (t["exit_reason"], t["exit_price"], t["exit_timestamp"]) == \
        ("SQUARE_OFF", 101.2, "2026-09-25T15:15:00")


def test_costs_and_slippage_against_us(tmp_path):
    t = exit_on(tmp_path, bar("AAA", 10, 17, 101, 108, 100.8, 107), costs=COSTS, slippage=5)
    assert t["entry_price"] == pytest.approx(100.05)       # open 100 + 5 bps
    assert t["exit_price"] == t["target"]                   # a limit fill has no slippage
    assert t["gross_pnl"] == pytest.approx((t["exit_price"] - t["entry_price"]) * 10, abs=1e-4)
    buy = COSTS.charges("BUY", 10, t["entry_price"]).total
    sell = COSTS.charges("SELL", 10, t["exit_price"]).total
    assert t["charges"] == pytest.approx(buy + sell, abs=1e-4)
    assert t["net_pnl"] == round(t["gross_pnl"] - t["charges"], 4)   # fields reconcile


def test_limits_record_missed_signals_once(tmp_path):
    recs = [rec(f"S{i}", rank=i + 1) for i in range(5)]
    h = Harness(tmp_path, {T(10, 16): recs, T(10, 17): recs})
    h.step(10, 16, *[bar(f"S{i}", 10, 15, 99, 100, 98, 99.5) for i in range(5)])
    h.step(10, 17)                                          # same signals again next minute
    missed = h.journal.missed()
    assert [(m["symbol"], m["reason"]) for m in missed] == [
        ("S3", "max open positions (3)"), ("S4", "max open positions (3)")]
    assert missed[0]["score"] == 70.0 and missed[0]["signal_at"] == "2026-09-25T10:16:00"


def test_broker_rejection_is_a_missed_signal(tmp_path):
    h = Harness(tmp_path, {T(10, 16): [rec()]}, capital=500)
    h.step(10, 16, bar("AAA", 10, 15, 99, 100, 98, 99.5))
    [m] = h.journal.missed()
    assert m["reason"].startswith("broker: insufficient capital")


def test_unfilled_pending_at_close_is_missed_not_invented(tmp_path):
    h = Harness(tmp_path, {T(10, 16): [rec()]})
    h.step(10, 16, bar("AAA", 10, 15, 99, 100, 98, 99.5))
    h.session.end_day(DAY.date())
    assert h.journal.trades() == []
    assert h.journal.missed()[0]["reason"] == "no bar after signal (no fill)"


def test_open_positions_closed_at_day_end(tmp_path):
    h = entered(tmp_path)
    h.session.end_day(DAY.date())
    assert (h.trade()["exit_reason"], h.trade()["exit_price"]) == ("SQUARE_OFF", 100.5)


def test_entry_snapshot_preserved(tmp_path):
    t = exit_on(tmp_path, bar("AAA", 10, 17, 101, 108, 100.8, 107))
    assert (t["recommendation_score"], t["category"], t["best_setup"], t["setup_state"]) == \
        (70.0, "CANDIDATE", "ORB15", "TRIGGERED")
    assert t["sector_context"] == {"verdict": "CONFIRMED"} and t["direction"] == "LONG"
    assert (t["strategy_version"], t["git_commit"], t["replay_or_live"]) == \
        ("v1", "deadbee", "replay")
    assert t["entry_reason"] == "Checked: technicals ✓"
