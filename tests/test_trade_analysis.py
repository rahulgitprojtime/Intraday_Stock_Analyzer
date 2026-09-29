"""Trade-level breakdown of a plan-study run (DECISIONS #32 analysis)."""

from math import sqrt

import pytest

from src.research import trade_analysis as ta


def test_bucket_totals_win_rate_profit_factor_and_drawdown():
    b = ta.Bucket()
    b.add("2026-01-01", gross=300.0, charges=75.0, slip=80.0, net=145.0)
    b.add("2026-01-02", gross=-200.0, charges=75.0, slip=80.0, net=-355.0)
    b.add("2026-01-03", gross=100.0, charges=75.0, slip=40.0, net=-15.0)
    m = b.result()
    assert (m["trades"], m["gross"], m["costs"], m["net"]) == (3, 200.0, 425.0, -225.0)
    assert (m["charges"], m["slippage"]) == (225.0, 200.0)
    assert m["avg_gross"] == pytest.approx(200 / 3) and m["avg_net"] == pytest.approx(-75.0)
    assert m["win_rate"] == pytest.approx(100 / 3)
    assert m["pf"] == pytest.approx(145 / 370)
    assert m["max_dd"] == pytest.approx(370.0)          # +145, then -355 and -15


def test_t_stat_uses_day_clustered_errors_and_halves_split_by_date():
    b = ta.Bucket()
    for day, g in (("2026-01-05", 10.0), ("2026-01-05", 30.0), ("2026-03-02", -20.0), ("2026-03-03", 40.0)):
        b.add(day, gross=g, charges=0.0, slip=0.0, net=g)
    m = b.result(split="2026-03-01")
    mean = 60 / 4
    resid = [(40 - 2 * mean), (-20 - mean), (40 - mean)]         # per-day totals less n x mean
    se = sqrt(sum(x * x for x in resid) * 3 / 2) / 4
    assert m["t_gross"] == pytest.approx(mean / se)
    assert (m["avg_gross_h1"], m["avg_gross_h2"]) == (20.0, 10.0)


def test_band_labels():
    assert [ta.entry_band(t) for t in ("09:25", "09:30", "10:59", "14:30")] == \
        ["09:25-09:29", "09:30-09:59", "10:30-10:59", "14:00-14:30"]
    assert [ta.hold_band(m) for m in (0, 4, 5, 59, 60, 345)] == \
        ["0-4 min", "0-4 min", "5-14 min", "30-59 min", "60-119 min", "240+ min"]
    assert ta.weekday("2026-02-01") == "Sun" and ta.weekday("2026-03-02") == "Mon"
    assert [ta.rvol_band(x) for x in ("", "0.5", "1", "12")] == ["n/a", "<1", "1-2", ">=10"]
    assert [ta.price_band(p) for p in (99.0, 250.0, 2600.0)] == ["<250", "250-500", ">=2500"]
    assert ta.value_band("") == "n/a" and ta.value_band("75.5") == "50-200 cr"


def test_breakdown_assigns_rows_to_populations(tmp_path):
    base = {"day": "2026-01-05", "symbol": "AAA", "setup": "ORB", "side": "LONG",
            "pattern": "MARUBOZU", "entry_time": "09:25", "entry": "500", "rvol5": "3",
            "market": "NORMAL", "mtf": "1", "regime": "UP", "day_type": "UP", "avg_value_cr": "40"}
    exits = {f"{e}_{k}": v for e in ta.EXITS
             for k, v in (("gross", "100"), ("charges", "75"), ("slippage", "80"), ("net", "-55"),
                          ("reason", "TRAIL"), ("hold", "30"))}
    rows = [base | exits | {"candle": "1", "sel_ALL": "1"},
            base | exits | {"candle": "1", "sel_ALL": "0", "symbol": "BBB"},
            base | exits | {"candle": "0", "sel_ALL": "0", "pattern": ""}]
    got = ta.breakdown(rows)
    assert got[("POOL", "T4", "total", "all")].n == 2
    assert got[("PLAN", "T4", "setup", "ORB")].n == 1
    assert got[("CONTROL", "T4", "candle confirmation", "no pattern")].n == 1
    assert got[("POOL", "T3", "exit reason", "TRAIL")].n == 2
    assert got[("POOL", "T4", "stock", "BBB")].n == 1
    assert ("PLAN", "T4", "stock", "AAA") not in got
