from datetime import datetime, timedelta

from src.paper.orders import Bar
from src.paper.shortlist import ShortlistConfig, opening_shortlist

D0, D1 = datetime(2026, 9, 24, 9, 15), datetime(2026, 9, 25, 9, 15)
CFG = ShortlistConfig(top_n=2, window_minutes=15, min_turnover=1_000)


def session(sym, start, price, vol, minutes=30, open_=None):
    out = []
    for i in range(minutes):
        o = open_ if (open_ is not None and i == 0) else price
        out.append(Bar(sym, start + timedelta(minutes=i), o, price + 1, price - 1, price, vol))
    return out


def test_ranks_by_turnover_gap_and_relative_volume():
    today = {"BIG": session("BIG", D1, 100, 1000, open_=103),     # 3% gap, 2x volume
             "MID": session("MID", D1, 100, 500),
             "LOW": session("LOW", D1, 100, 100)}
    prior = {s: session(s, D0, 100, 500) for s in today}
    rows = opening_shortlist(today, prior, CFG)
    assert [r["symbol"] for r in rows] == ["BIG", "MID"]            # top_n = 2
    big = rows[0]
    assert big["rank"] == 1 and big["gap_pct"] == 3.0 and big["rvol"] == 2.0


def test_liquidity_floor_and_only_the_window_counts():
    today = {"THIN": session("THIN", D1, 10, 1), "OK": session("OK", D1, 100, 100)}
    rows = opening_shortlist(today, {}, CFG)
    assert [r["symbol"] for r in rows] == ["OK"]
    assert rows[0]["turnover"] == 15 * 100 * 100                    # 15 window minutes only
    assert rows[0]["gap_pct"] is None and rows[0]["rvol"] is None   # no prior session


def test_gap_down_counts_as_in_play_too():
    today = {"UP": session("UP", D1, 100, 100, open_=101),
             "DOWN": session("DOWN", D1, 100, 100, open_=96)}
    prior = {s: session(s, D0, 100, 100) for s in today}
    assert opening_shortlist(today, prior, CFG)[0]["symbol"] == "DOWN"
