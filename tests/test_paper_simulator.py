from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.paper.journal import Journal
from src.paper.policy import PaperConfig
from src.paper.simulator import PaperSimulator
from src.utils.config import load_yaml

RAW = load_yaml("paper.yaml")
NO_COST = PaperConfig.from_dict(RAW | {"fills": RAW["fills"] | {"slippage_bps": 0,
                                                                 "charges_pct_round_trip": 0}})
DAY = datetime(2026, 9, 25)
T = lambda h, m: DAY.replace(hour=h, minute=m)  # noqa: E731
VERSIONS = {"strategy_version": "v1", "config_hash": "abc", "git_commit": "deadbee"}


def bar(sym, h, m, o, hi, lo, c):
    return Candle(Instrument(sym, Exchange.NSE, Segment.CASH), 1, T(h, m), o, hi, lo, c, 1000)


def rec(sym="AAA", rank=1, **kw):
    return {"symbol": sym, "mode": "DAY", "category": "CANDIDATE", "score": 70.0, "rank": rank,
            "eligible_for_top_n": True, "profile": "IN_PLAY_STRONG_SETUP",
            "setup": {"best": "ORB15", "best_state": "TRIGGERED",
                      "confluence_families": ["price_structure"]},
            "quantitative": {"is_in_play": True, "in_play_score": 60.0, "microstructure": None},
            "data_quality": {"status": "OK"}, "market_context": {"score": 55.0},
            "sector_context": {"verdict": "CONFIRMED"}, "qualitative": {"status": "unavailable"},
            "prerequisites": [], "prerequisites_summary": "Checked: technicals ✓"} | kw


def sim(tmp_path, cfg=NO_COST):
    return PaperSimulator(cfg, Journal(tmp_path / "j.jsonl"), "run1", VERSIONS, "replay")


def trade(s):
    [t] = s.journal.trades()
    return t


def test_fill_at_next_bar_open_then_target(tmp_path):
    s = sim(tmp_path)
    atr = {"AAA": 20.0}                                    # risk = 5 → stop 95, target 107.5
    s.step(T(10, 16), [rec()], {"AAA": [bar("AAA", 10, 15, 99, 100, 98, 99.5)]}, atr)
    assert s.journal.trades() == []                        # signal only; no same-bar fill
    bars = [bar("AAA", 10, 15, 99, 100, 98, 99.5), bar("AAA", 10, 16, 100, 101, 99.5, 100.5)]
    s.step(T(10, 17), [], {"AAA": bars}, atr)
    t = trade(s)
    assert (t["entry_price"], t["entry_timestamp"]) == (100.0, "2026-09-25T10:16:00")
    assert (t["stop_loss"], t["target"], t["stop_method"]) == (95.0, 107.5, "atr")
    assert t["signal_at"] == "2026-09-25T10:16:00" and t["exit_reason"] is None
    bars.append(bar("AAA", 10, 17, 101, 108, 100.8, 107))
    s.step(T(10, 18), [], {"AAA": bars}, atr)
    t = trade(s)
    assert (t["exit_reason"], t["exit_price"]) == ("TARGET", 107.5)
    assert t["gross_pnl"] == pytest.approx(75.0) and t["risk_multiple"] == pytest.approx(1.5)
    assert t["holding_minutes"] == 2 and t["pnl_percent"] == pytest.approx(7.5)


def run_one(tmp_path, exit_bar, atr=20.0, cfg=NO_COST):
    s = sim(tmp_path, cfg)
    b0 = bar("AAA", 10, 15, 99, 100, 98, 99.5)
    s.step(T(10, 16), [rec()], {"AAA": [b0]}, {"AAA": atr})
    b1 = bar("AAA", 10, 16, 100, 101, 99.5, 100.5)
    s.step(T(10, 17), [], {"AAA": [b0, b1]}, {"AAA": atr})
    s.step(T(10, 18), [], {"AAA": [b0, b1, exit_bar]}, {"AAA": atr})
    return trade(s)


def test_stop_loss(tmp_path):
    t = run_one(tmp_path, bar("AAA", 10, 17, 99, 99.5, 94, 95))
    assert (t["exit_reason"], t["exit_price"], t["risk_multiple"]) == ("STOP_LOSS", 95.0, -1.0)


def test_same_candle_ambiguity_counts_as_stop(tmp_path):
    t = run_one(tmp_path, bar("AAA", 10, 17, 100, 110, 90, 105))
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == 95.0
    assert t["exit_note"] == "stop and target touched in one bar: stop assumed first"


def test_gap_through_stop_fills_at_the_worse_open(tmp_path):
    t = run_one(tmp_path, bar("AAA", 10, 17, 92, 93, 91, 92.5))
    assert (t["exit_reason"], t["exit_price"]) == ("STOP_LOSS", 92.0)


def test_end_of_day_exit_at_last_closed_bar(tmp_path):
    s = sim(tmp_path)
    b0 = bar("AAA", 10, 15, 99, 100, 98, 99.5)
    s.step(T(10, 16), [rec()], {"AAA": [b0]}, {"AAA": 20.0})
    b1 = bar("AAA", 10, 16, 100, 101, 99.5, 100.5)
    s.step(T(10, 17), [], {"AAA": [b0, b1]}, {"AAA": 20.0})
    late = bar("AAA", 15, 15, 101, 102, 100, 101.2)       # data gap after 15:16 (Groww)
    s.step(T(15, 20), [], {"AAA": [b0, b1, late]}, {"AAA": 20.0})
    t = trade(s)
    assert (t["exit_reason"], t["exit_price"], t["exit_timestamp"]) == \
        ("END_OF_DAY", 101.2, "2026-09-25T15:16:00")


def test_costs_and_slippage_against_us(tmp_path):
    cfg = PaperConfig.from_dict(RAW)                      # 5 bps each way, 0.05% charges
    t = run_one(tmp_path, bar("AAA", 10, 17, 101, 108, 100.8, 107), cfg=cfg)
    assert t["entry_price"] == pytest.approx(100.05)      # open 100 + 5 bps
    assert t["exit_price"] == pytest.approx(t["target"] * (1 - 0.0005))
    gross = (t["exit_price"] - t["entry_price"]) * 10
    assert t["gross_pnl"] == pytest.approx(gross, abs=1e-4)          # money kept to 4 dp
    assert t["charges"] == pytest.approx(100.05 * 10 * 0.0005, abs=1e-4)
    assert t["net_pnl"] == round(t["gross_pnl"] - t["charges"], 4)   # fields reconcile


def test_limits_record_missed_signals_once(tmp_path):
    s = sim(tmp_path)
    recs = [rec(f"S{i}", rank=i + 1) for i in range(5)]
    bars = {f"S{i}": [bar(f"S{i}", 10, 15, 99, 100, 98, 99.5)] for i in range(5)}
    s.step(T(10, 16), recs, bars, {})
    s.step(T(10, 17), recs, bars, {})                     # same signals again next minute
    missed = s.journal.missed()
    assert [(m["symbol"], m["reason"]) for m in missed] == [
        ("S3", "max open positions (3)"), ("S4", "max open positions (3)")]
    assert missed[0]["score"] == 70.0 and missed[0]["signal_at"] == "2026-09-25T10:16:00"


def test_unfilled_pending_at_close_is_missed_not_invented(tmp_path):
    s = sim(tmp_path)
    s.step(T(10, 16), [rec()], {"AAA": [bar("AAA", 10, 15, 99, 100, 98, 99.5)]}, {})
    s.finish()
    assert s.journal.trades() == []
    assert s.journal.missed()[0]["reason"] == "no bar after signal (no fill)"


def test_open_positions_closed_by_finish(tmp_path):
    s = sim(tmp_path)
    b0 = bar("AAA", 10, 15, 99, 100, 98, 99.5)
    s.step(T(10, 16), [rec()], {"AAA": [b0]}, {"AAA": 20.0})
    b1 = bar("AAA", 10, 16, 100, 101, 99.5, 100.5)
    s.step(T(10, 17), [], {"AAA": [b0, b1]}, {"AAA": 20.0})
    s.finish()
    assert trade(s)["exit_reason"] == "END_OF_DAY" and trade(s)["exit_price"] == 100.5


def test_entry_snapshot_preserved(tmp_path):
    t = run_one(tmp_path, bar("AAA", 10, 17, 101, 108, 100.8, 107))
    assert (t["recommendation_score"], t["category"], t["best_setup"], t["setup_state"]) == \
        (70.0, "CANDIDATE", "ORB15", "TRIGGERED")
    assert t["sector_context"] == {"verdict": "CONFIRMED"} and t["direction"] == "LONG"
    assert (t["strategy_version"], t["git_commit"], t["replay_or_live"]) == \
        ("v1", "deadbee", "replay")
    assert t["entry_reason"] == "Checked: technicals ✓"
