"""The user's trading plan on the whole market (DECISIONS #32)."""

import csv
from datetime import date

import pytest

from src.research import plan_backtest as pb
from src.research import setup_backtest as sb
from src.research.setup_backtest import K
from src.utils.config import load_yaml
from tests.test_setup_backtest import write_store

CURVE = [(i + 1) / 375 for i in range(375)]


def flat(start, px=100.0, end=375):
    return [K(t, px, px, px, px, 1) for t in range(start, end)]


# -- trailing stop ---------------------------------------------------------------------

def test_trailing_stop_follows_the_high_and_exits_on_the_pullback():
    bars = [K(10, 100, 100.5, 99.5, 100.2, 1), K(11, 100.2, 104, 100.1, 103.8, 1),
            K(12, 103.8, 104.2, 101.5, 101.9, 1)]
    x = pb.trail_exit(bars, 0, 100.0, 2.0)
    assert (x.t, x.reason, x.price, x.raw) == (12, "TRAIL", sb._sell(102.0), 102.0)


def test_a_bar_faces_the_stop_set_before_it_opened():
    bars = [K(10, 100, 110, 99, 100, 1)] + flat(11)
    x = pb.trail_exit(bars, 0, 100.0, 2.0)
    assert (x.t, x.reason, x.price) == (11, "TRAIL", sb._sell(100.0))    # stop 108 from bar 10's high


def test_initial_stop_target_and_square_off():
    assert pb.trail_exit([K(10, 100, 101, 97.9, 98, 1)], 0, 100.0, 2.0, 103.0).reason == "STOP"
    hit = pb.trail_exit([K(10, 100, 103.5, 99, 103, 1)], 0, 100.0, 2.0, 103.0)
    assert (hit.reason, hit.price, hit.raw) == ("TARGET", 103.0, 103.0)     # a limit: no slippage
    x = pb.trail_exit([K(10, 100, 100.5, 99.5, 100.2, 1)] + flat(11, 100.3), 0, 100.0, 2.0, 103.0)
    assert (x.reason, x.t, x.price, x.raw) == ("SQUARE_OFF", 360, sb._sell(100.3), 100.3)


def test_short_trailing_stop_works_in_the_mirror():
    real = [K(10, 100, 100.2, 99, 99.2, 1), K(11, 99.2, 99.3, 95, 95.2, 1),
            K(12, 95.2, 97.5, 95.1, 97.4, 1)]
    v = sb.mirror(real)
    fill = sb._buy(v[0].o)                                  # a short sells at 99.95
    x = pb.trail_exit(v, 0, fill, 0.02 * abs(fill))
    assert (x.reason, x.t) == ("TRAIL", 12)
    assert -x.price == pytest.approx((95 + 0.02 * 99.95) * 1.0005, abs=1e-3)   # bought back


# -- market gate ------------------------------------------------------------------------

def index_bars(pct, base=20000.0):
    c = base * (1 + pct / 100)
    return [K(t, base if t == 0 else c, max(base, c), min(base, c), c, 0) for t in range(375)]


def gate(pct_nifty, pct_bank, volume_rate):
    avg = 375_000.0
    stock = [K(t, 100, 100, 100, 100, volume_rate * avg / 375) for t in range(375)]
    return pb.MarketGate([index_bars(pct_nifty), index_bars(pct_bank)], [(stock, avg)], CURVE)


def test_market_gate_states_and_daily_caps():
    quiet = gate(0.1, -0.2, 0.5)
    assert quiet.state(60) == "QUIET" and quiet.cap(60) == 0 and quiet.cap(60, gate=False) == 3
    assert gate(0.3, 0.1, 0.5).state(60) == "NORMAL"               # moving, thin volume
    assert gate(0.1, 0.2, 1.5).state(60) == "NORMAL"               # flat, normal volume
    strong = gate(0.2, -0.6, 1.5)
    assert strong.state(60) == "STRONG" and strong.cap(60) == 5
    assert gate(0.6, 0.0, 0.9).cap(60) == 3                         # big move, below-normal volume


# -- selection --------------------------------------------------------------------------

class FixedGate:
    def __init__(self, caps):
        self.caps = caps                                  # [(from minute, cap)]

    def cap(self, t, gate=True):
        return [c for start, c in self.caps if t >= start][-1]


def cand(sym, t, rvol, setup="VWAP", side="LONG"):
    return pb.Candidate("2026-01-01", sym, setup, side, "", t, t, 100.0, 800, rvol)


def test_selection_takes_in_play_stocks_first_one_per_stock_within_the_cap():
    cands = [cand("A", 15, 1.0), cand("B", 15, 3.0), cand("C", 15, 2.0), cand("D", 15, 0.5),
             cand("B", 20, 9.0, setup="EMA"), cand("E", 120, None), cand("F", 125, 1.0)]
    picked = pb.select(cands, FixedGate([(0, 3), (100, 5)]), sb.SETUPS)
    assert [c.symbol for c in picked] == ["B", "C", "A", "E", "F"]


def test_a_quiet_market_blocks_entries_until_it_wakes_up():
    cands = [cand("A", 15, 2.0), cand("B", 90, 1.0), cand("C", 95, 1.0, setup="ORB")]
    assert [c.symbol for c in pb.select(cands, FixedGate([(0, 0), (60, 3)]), sb.SETUPS)] == ["B", "C"]
    assert [c.symbol for c in pb.select(cands, FixedGate([(0, 3)]), ("ORB",))] == ["C"]


# -- end to end -------------------------------------------------------------------------

def test_plan_chunk_selects_trades_and_summarises_the_portfolio(tmp_path):
    days = [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
    write_store(tmp_path / "store", days)
    rows_csv, days_csv = tmp_path / "rows.csv", tmp_path / "days.csv"
    counts = pb.run_plan_chunk(str(tmp_path / "store"), [days[2].isoformat()],
                               [d.isoformat() for d in days[:2]], str(rows_csv), str(days_csv),
                               load_yaml("costs.yaml"), CURVE)
    assert counts["stock_days"] == 1
    rows = list(csv.DictReader(rows_csv.open(encoding="utf-8")))
    [orb] = [r for r in rows if r["setup"] == "ORB" and r["side"] == "LONG" and r["candle"] == "1"]
    assert (orb["sel_ALL"], orb["sel_ORB"], orb["sel_VWAP"]) == ("1", "1", "0")
    assert float(orb["qty"]) == 80_000 // 101.9
    # diagnostic tags (DECISIONS #32 analysis): they never change which trades are taken
    controls = [r for r in rows if r["candle"] == "0"]
    assert controls and all(r[f"sel_{p}"] == "0" for r in controls for p in pb.PORTFOLIOS)
    assert (orb["regime"], orb["day_type"], orb["avg_value_cr"]) == ("SIDEWAYS", "UP", "1.0")
    assert orb["mtf"] in ("0", "1") and orb["T3_hold"] == str(360 - 15)
    gross, ch, slip, net = (float(orb[f"T3_{k}"]) for k in ("gross", "charges", "slippage", "net"))
    assert gross == pytest.approx(net + ch + slip, abs=1e-3) and slip > 0
    ports, signals = pb.summarize_plan(rows, [days[2].isoformat()])
    m = ports[("ALL", "T3")]
    assert (m["trades"], m["days_traded"], m["days_skipped"]) == (1, 1, 0)
    assert m["return_pct"] == pytest.approx(100 * m["net"] / pb.BUDGET)
    assert m["before_costs"] == pytest.approx(gross)
    assert ports[("VWAP", "T3")]["trades"] == 0
    assert signals[("ORB", "LONG", "TRAIL")]["trades"] == 1
    [d] = list(csv.DictReader(days_csv.open(encoding="utf-8")))
    assert d["n_ALL"] == "1" and d["state_0930"] in ("QUIET", "NORMAL", "STRONG")
