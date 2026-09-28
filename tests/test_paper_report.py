import json

import pytest

from src.paper.metrics import NA, SMALL_SAMPLE_N, breakdowns, summarize, time_bucket
from src.paper.report import write_daily_report

BANNED = ("good trade", "bad trade", "probability of profit", "guaranteed", "caused")


def t(net, r, setup="ORB15", entry="2026-09-25T10:16:00", exit_="2026-09-25T10:40:00",
      hold=24, reason="TARGET", sector="CONFIRMED", news=None, market=55.0):
    return {"trade_id": f"{setup}{entry}{net}", "symbol": "AAA", "net_pnl": net,
            "gross_pnl": net, "risk_multiple": r, "best_setup": setup, "entry_timestamp": entry,
            "exit_timestamp": exit_, "holding_minutes": hold, "exit_reason": reason,
            "sector_context": {"verdict": sector}, "market_context": {"score": market},
            "qualitative_context": news or {"status": "unavailable"}}


TRADES = [t(100, 1.5), t(-50, -1.0, reason="STOP_LOSS"), t(0, 0.0, reason="END_OF_DAY"),
          t(40, 0.6, setup="VWAP_RECLAIM", entry="2026-09-25T13:00:00", sector="WEAK"),
          t(-80, -1.2, setup="VWAP_RECLAIM", entry="2026-09-25T09:20:00", reason="STOP_LOSS")]


def test_summary_metrics_hand_computed():
    s = summarize(TRADES)
    assert (s["trades"], s["winners"], s["losers"], s["breakeven"]) == (5, 2, 2, 1)
    assert s["win_rate"] == pytest.approx(40.0)
    assert s["net_pnl"] == 10 and s["average_pnl"] == 2 and s["median_pnl"] == 0
    assert (s["average_winner"], s["average_loser"]) == (70, -65)
    assert (s["largest_winner"], s["largest_loser"]) == (100, -80)
    assert s["profit_factor"] == pytest.approx(140 / 130)
    assert s["average_r"] == pytest.approx((1.5 - 1 + 0 + 0.6 - 1.2) / 5)
    assert s["median_r"] == 0
    assert s["max_drawdown"] == pytest.approx(90)      # equity by exit order: 100,50,50,90,10
    assert s["average_holding_minutes"] == 24
    assert s["sample_warning"] == "INSUFFICIENT SAMPLE SIZE"


def test_unavailable_statistics_say_so():
    s = summarize([t(100, 1.5)])
    assert s["profit_factor"] == NA and s["average_loser"] == NA
    empty = summarize([])
    assert empty["trades"] == 0 and empty["win_rate"] == NA and empty["result"] == "NO TRADES"


def test_breakdowns_include_losing_groups_with_sample_sizes():
    b = breakdowns(TRADES)
    assert b["setup"]["VWAP_RECLAIM"]["trades"] == 2
    assert b["setup"]["VWAP_RECLAIM"]["net_pnl"] == -40                    # loss shown
    assert b["setup"]["ORB15"]["sample_warning"] == "SMALL SAMPLE"
    assert b["setup_family"]["price_structure"]["trades"] == 3
    assert b["sector"]["WEAK"]["trades"] == 1
    assert b["news"]["NOT_AVAILABLE"]["trades"] == 5
    assert list(b["time_of_day"]) == ["09:15-09:30", "09:30-10:30", "10:30-11:30",
                                      "11:30-13:30", "13:30-14:30", "14:30-15:00", "15:00-15:30"]
    assert b["time_of_day"]["13:30-14:30"]["trades"] == 0                  # empty shown, not hidden
    assert b["time_of_day"]["09:30-10:30"]["trades"] == 3
    assert b["exit_reason"]["STOP_LOSS"]["trades"] == 2


@pytest.mark.parametrize("hhmm,bucket", [("09:15", "09:15-09:30"), ("09:30", "09:30-10:30"),
                                         ("11:29", "10:30-11:30"), ("13:30", "13:30-14:30"),
                                         ("14:59", "14:30-15:00"), ("15:05", "15:00-15:30")])
def test_time_buckets(hhmm, bucket):
    assert time_bucket(f"2026-09-25T{hhmm}:00") == bucket


def test_daily_report_files_losses_and_language(tmp_path):
    out = write_daily_report(tmp_path, "2026-09-25", TRADES, missed=[{"reason": "x"}],
                             meta={"strategy_version": "v1", "replay_or_live": "replay"})
    for name in ("paper_trades.json", "daily_report.json", "daily_report.md"):
        assert (out / name).exists()
    data = json.loads((out / "daily_report.json").read_text(encoding="utf-8"))
    assert data["summary"]["net_pnl"] == 10 and len(data["trades"]) == 5
    assert data["missed_signals"] == 1
    md = (out / "daily_report.md").read_text(encoding="utf-8")
    assert "PAPER TRADING / SIMULATION" in md and "-80" in md and "INSUFFICIENT SAMPLE SIZE" in md
    assert "Observed association" in md
    assert not [p for p in BANNED if p in md.lower()]


def test_no_trade_day_report(tmp_path):
    out = write_daily_report(tmp_path, "2026-09-25", [], missed=[], meta={})
    assert "NO TRADES" in (out / "daily_report.md").read_text(encoding="utf-8")


def test_small_sample_threshold_is_explicit():
    assert SMALL_SAMPLE_N == 30
