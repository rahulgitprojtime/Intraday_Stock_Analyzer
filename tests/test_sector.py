import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.sector import (
    CONFIRMED,
    NEUTRAL,
    UNAVAILABLE,
    WEAK,
    SectorConfig,
    change_since_open,
    load_sector_map,
    sector_snapshot,
    stock_context,
)
from src.utils.config import load_strategy, load_universe

T0 = datetime(2026, 9, 25, 9, 15)
AS_OF = T0 + timedelta(minutes=30)
CFG = SectorConfig()


def bars(sym, pct, n=30, index=False):
    """n closed 1-min bars from 100 to 100*(1+pct/100)."""
    inst = Instrument(sym, Exchange.NSE, Segment.CASH, is_index=index)
    out = []
    for i in range(n):
        c = 100 * (1 + pct / 100 * (i + 1) / n)
        o = 100 if i == 0 else out[-1].close
        out.append(Candle(inst, 1, T0 + timedelta(minutes=i), o, max(o, c), min(o, c), c, 0))
    return out


SECTORS = {"IT": {"index": "NIFTYIT", "members": ["INFY", "TCS", "WIPRO", "HCLTECH"]},
           "METAL": {"index": "NIFTYMETAL", "members": ["TATASTEEL"]}}


def snap(it_pct, peers, metal_pct=0.0, nifty_pct=0.0, stale_index=False):
    idx = {"NIFTYIT": bars("NIFTYIT", it_pct, index=True),
           "NIFTYMETAL": bars("NIFTYMETAL", metal_pct, index=True)}
    if stale_index:
        idx["NIFTYIT"] = idx["NIFTYIT"][:10]              # last bar 09:24 → stale at 09:45
    stocks = {s: bars(s, p) for s, p in peers.items()}
    return sector_snapshot(SECTORS, idx, stocks, change_since_open(bars("NIFTY", nifty_pct)),
                           AS_OF, 120, CFG)


def test_change_since_open():
    assert change_since_open(bars("X", 1.0)) == pytest.approx(1.0)
    assert change_since_open([]) is None


def test_confirmed_needs_index_rs_and_peer_breadth():
    s = snap(0.8, {"INFY": 1.0, "TCS": 0.5, "WIPRO": 0.4, "HCLTECH": -0.2}, nifty_pct=0.3)
    ctx = stock_context("INFY", s, bars("INFY", 1.0), CFG)
    assert ctx["verdict"] == CONFIRMED
    assert ctx["sector_relative_strength"] == pytest.approx(0.5)
    assert (ctx["peers_up"], ctx["peers_total"]) == (2, 3)             # INFY is not its own peer
    assert ctx["stock_vs_sector"] == pytest.approx(0.2)


def test_weak_by_index_or_by_peers():
    lag = snap(-0.3, {"INFY": 0.5, "TCS": 0.5, "WIPRO": 0.5}, nifty_pct=0.0)
    assert stock_context("INFY", lag, bars("INFY", 0.5), CFG)["verdict"] == WEAK
    peers_down = snap(0.1, {"INFY": 0.5, "TCS": -0.5, "WIPRO": -0.4, "HCLTECH": -0.1})
    assert stock_context("INFY", peers_down, bars("INFY", 0.5), CFG)["verdict"] == WEAK


def test_neutral_between_thresholds():
    s = snap(0.1, {"INFY": 0.5, "TCS": 0.5, "WIPRO": -0.5})
    assert stock_context("INFY", s, bars("INFY", 0.5), CFG)["verdict"] == NEUTRAL


def test_threshold_boundaries_inclusive():
    at_confirm = snap(0.2, {"INFY": 1.0, "TCS": 0.5, "WIPRO": 0.5, "HCLTECH": -0.1})   # 2/3 up
    assert stock_context("INFY", at_confirm, bars("INFY", 1), CFG)["verdict"] == CONFIRMED
    at_weak = snap(-0.2, {"INFY": 1.0})
    assert stock_context("INFY", at_weak, bars("INFY", 1), CFG)["verdict"] == WEAK


def test_single_member_sector_uses_index_only():
    s = snap(0.0, {"TATASTEEL": 1.0}, metal_pct=0.5)
    ctx = stock_context("TATASTEEL", s, bars("TATASTEEL", 1.0), CFG)
    assert ctx["verdict"] == CONFIRMED and ctx["peers_total"] == 0


def test_fewer_than_two_usable_peers_uses_index_only():
    s = snap(0.5, {"INFY": 1.0, "TCS": -1.0})                       # 1 peer only
    assert stock_context("INFY", s, bars("INFY", 1.0), CFG)["verdict"] == CONFIRMED


def test_unavailable_when_unmapped_stale_or_no_nifty():
    s = snap(0.5, {"INFY": 1.0, "TCS": 1.0})
    assert stock_context("LT", s, bars("LT", 1.0), CFG) == {
        "status": "unavailable", "sector": None, "index": None, "sector_score": None,
        "sector_relative_strength": None, "stock_vs_sector": None, "peers_up": None,
        "peers_total": None, "verdict": UNAVAILABLE,
        "sector_market_alignment": None, "reason": "no sector index for this stock"}
    stale = snap(0.5, {"INFY": 1.0}, stale_index=True)
    assert stock_context("INFY", stale, bars("INFY", 1), CFG)["verdict"] == UNAVAILABLE
    no_nifty = sector_snapshot(SECTORS, {"NIFTYIT": bars("NIFTYIT", 0.5, index=True)}, {},
                               None, AS_OF, 120, CFG)
    assert stock_context("INFY", no_nifty, bars("INFY", 1), CFG)["verdict"] == UNAVAILABLE


def test_sector_score_ramps():
    strong = snap(1.0, {"INFY": 1, "TCS": 1, "WIPRO": 1})
    weak = snap(-1.0, {"INFY": 1, "TCS": -1, "WIPRO": -1})
    assert stock_context("INFY", strong, bars("INFY", 1), CFG)["sector_score"] == 100
    assert stock_context("INFY", weak, bars("INFY", 1), CFG)["sector_score"] == 0


def test_config_from_strategy_yaml():
    assert SectorConfig.from_dict(load_strategy()["sector"]) == SectorConfig()


def test_sectors_yaml_members_in_universe_and_indices_exist():
    sectors, problems = load_sector_map(load_universe()["symbols"])
    assert problems == []
    members = [m for s in sectors.values() for m in s["members"]]
    assert len(members) == len(set(members))                        # one sector per stock
    csv_path = Path("data/cache/groww_instruments.csv")
    if not csv_path.exists():
        pytest.skip("instrument cache not downloaded")
    with csv_path.open(encoding="utf-8") as f:
        idx = {r["trading_symbol"] for r in csv.DictReader(f)
               if r["exchange"] == "NSE" and r["instrument_type"] == "IDX"}
    assert {s["index"] for s in sectors.values()} <= idx


def test_bad_sector_map_entries_are_reported_and_skipped():
    raw = {"A": {"index": "NIFTYIT", "members": ["INFY", "NOTINUNIVERSE"]},
           "B": {"index": "NIFTYAUTO", "members": ["INFY", "MARUTI"]},
           "C": {"members": ["TCS"]}}
    sectors, problems = load_sector_map(["INFY", "MARUTI", "TCS"], raw)
    assert sectors == {"A": {"index": "NIFTYIT", "members": ["INFY"]},
                       "B": {"index": "NIFTYAUTO", "members": ["MARUTI"]}}
    assert len(problems) == 3
