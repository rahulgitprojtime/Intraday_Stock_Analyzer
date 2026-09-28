from datetime import date, datetime, time, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.quantitative.volume_scan import (
    ActiveSet,
    ScanFilters,
    daily_stats,
    expected_fraction,
    load_market_curve,
    rank_volume_change,
)

TODAY = date(2026, 9, 28)
CURVE = [(i + 1) / 375 for i in range(375)]          # linear curve keeps the arithmetic simple
FILTERS = ScanFilters(min_price=20.0, min_avg_daily_volume=500_000, min_avg_traded_value=5e7,
                      long_only=True)


def daily(sym, closes_vols, end=TODAY):
    inst = Instrument(sym, Exchange.NSE, Segment.CASH)
    out, d = [], end
    for c, v in reversed(closes_vols):
        out.append(Candle(inst, 1440, datetime.combine(d, time()), c, c, c, c, v))
        d -= timedelta(days=1)
    return list(reversed(out))


def test_daily_stats_exclude_today_and_use_last_sessions():
    bars = daily("AAA", [(100, 1_000_000)] * 20 + [(110, 9_999_999)])   # last bar = today
    s = daily_stats("AAA", bars, TODAY, sessions=20)
    assert (s.avg_volume, s.prev_close, s.sessions) == (1_000_000, 100, 20)
    assert s.avg_traded_value == pytest.approx(1e8)
    assert daily_stats("AAA", [], TODAY, 20) is None


def test_expected_fraction_clamps_to_session():
    assert expected_fraction(CURVE, time(9, 15)) == pytest.approx(1 / 375)
    assert expected_fraction(CURVE, time(12, 22)) == pytest.approx(188 / 375)
    assert expected_fraction(CURVE, time(9, 0)) == pytest.approx(1 / 375)
    assert expected_fraction(CURVE, time(16, 0)) == 1.0


def stats(sym, avg_vol=1_000_000, prev=100.0):
    return daily_stats(sym, daily(sym, [(prev, avg_vol)] * 20, TODAY - timedelta(days=1)),
                       TODAY, 20)


def q(sym, volume, last):
    return {"symbol": sym, "volume": volume, "last_price": last}


def test_rank_by_volume_change_long_only_and_liquidity():
    at = time(12, 22)                                   # half the normal day has traded
    quotes = [q("UP2X", 1_000_000, 105), q("UP4X", 2_000_000, 101), q("DOWN9X", 4_500_000, 95),
              q("THIN", 5_000_000, 120), q("PENNY", 3_000_000, 12)]
    st = {"UP2X": stats("UP2X"), "UP4X": stats("UP4X"), "DOWN9X": stats("DOWN9X"),
          "THIN": stats("THIN", avg_vol=100_000), "PENNY": stats("PENNY", prev=10.0)}
    ranked = rank_volume_change(quotes, st, FILTERS, at, CURVE)
    f = expected_fraction(CURVE, at)
    assert [r.symbol for r in ranked] == ["UP4X", "UP2X"]
    assert ranked[0].volume_change == pytest.approx(2_000_000 / (1_000_000 * f))
    assert ranked[1].volume_change == pytest.approx(1_000_000 / (1_000_000 * f))
    assert ranked[0].day_change_pct == pytest.approx(1.0)
    both = rank_volume_change(quotes, st, ScanFilters(20.0, 500_000, 5e7, long_only=False), at,
                              CURVE)
    assert both[0].symbol == "DOWN9X"                   # only the long-only filter removed it


def test_missing_stats_or_quote_is_skipped_not_guessed():
    ranked = rank_volume_change([q("NEW", 9_000_000, 50)], {}, FILTERS, time(10, 0), CURVE)
    assert ranked == []


def cands(*pairs):
    from src.quantitative.volume_scan import ScanCandidate
    return [ScanCandidate(s, v, 1.0, 100.0, 1) for s, v in pairs]


T0 = datetime(2026, 9, 28, 10, 0)


def test_active_set_top_n_with_minimum_stay_and_pins():
    a = ActiveSet(top_n=2, min_stay_minutes=10)
    assert a.update(T0, cands(("A", 5), ("B", 4), ("C", 3)), pinned=set()) == ["A", "B"]
    # C overtakes B 5 min later: B stays (entered < 10 min ago), C joins
    assert a.update(T0 + timedelta(minutes=5), cands(("A", 5), ("C", 4.5), ("B", 1)),
                    set()) == ["A", "C", "B"]
    # after the minimum stay B drops
    assert a.update(T0 + timedelta(minutes=11), cands(("A", 5), ("C", 4.5), ("B", 1)),
                    set()) == ["A", "C"]
    # a pinned symbol (open paper position) never drops
    assert a.update(T0 + timedelta(minutes=30), cands(("A", 5), ("C", 4.5)), {"B"}) == \
        ["A", "C", "B"]


def test_market_curve_file_is_a_monotone_fraction():
    curve = load_market_curve()
    assert len(curve) == 375 and curve[-1] == pytest.approx(1.0)
    assert all(b >= a for a, b in zip(curve, curve[1:]))
    assert 0 < curve[0] < 0.1                            # opening minute carries a real share
