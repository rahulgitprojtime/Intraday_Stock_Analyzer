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
    assert "DOWN9X" in [c.symbol for c in both]          # only the long-only filter removed it


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


def daily_hlc(sym, rows, end=TODAY - timedelta(days=1)):
    inst = Instrument(sym, Exchange.NSE, Segment.CASH)
    out, d = [], end
    for h, lo, c, v in reversed(rows):
        out.append(Candle(inst, 1440, datetime.combine(d, time()), None, h, lo, c, v))
        d -= timedelta(days=1)
    return list(reversed(out))


def test_daily_stats_atr_uses_high_low_close_even_without_opens():
    s = daily_stats("AAA", daily_hlc("AAA", [(102, 98, 100, 1_000_000)] * 20), TODAY, 20)
    assert s.atr == pytest.approx(4.0)


from src.quantitative.volume_scan import PRESCORE_WEIGHTS, prescore  # noqa: E402


def test_prescore_groups_from_a_quote():
    st = daily_stats("AAA", daily_hlc("AAA", [(102, 98, 100, 1_000_000)] * 20), TODAY, 20)
    at = datetime(2026, 9, 28, 12, 22)
    q = {"symbol": "AAA", "volume": 2_000_000, "last_price": 103.0, "open": 100.0,
         "high": 103.5, "low": 99.5, "average_price": 102.0,
         "prev_volume": 1_800_000, "prev_at": at - timedelta(minutes=3), "at": at}
    score, parts = prescore(q, st, CURVE)
    assert parts["movement"]["change"] == pytest.approx(100)          # +3% vs prev close
    assert parts["movement"]["range"] == pytest.approx(100)           # 4.0 range / 4.0 ATR
    assert parts["movement"]["position"] == pytest.approx((3.5 / 4 - 0.5) / 0.5 * 100)
    assert parts["movement"]["vwap"] == pytest.approx(98.04, abs=0.1)  # +0.98% above VWAP
    rate_recent, rate_day = 200_000 / 3, 2_000_000 / 187
    assert parts["volume"]["acceleration"] == pytest.approx(
        min(100, (rate_recent / rate_day - 1) / 2 * 100))
    assert 0 < score <= 100
    assert set(PRESCORE_WEIGHTS) == {"movement", "volume", "liquidity"}


def test_ranking_orders_by_prescore_not_volume_alone():
    at = time(12, 22)
    strong = {"symbol": "STRONG", "volume": 1_500_000, "last_price": 104.0, "open": 100.0,
              "high": 104.2, "low": 99.8}
    churn = {"symbol": "CHURN", "volume": 2_500_000, "last_price": 100.05, "open": 100.0,
             "high": 100.4, "low": 99.6}                                # heavy volume, no move
    st = {s: daily_stats(s, daily_hlc(s, [(102, 98, 100, 1_000_000)] * 20), TODAY, 20)
          for s in ("STRONG", "CHURN")}
    ranked = rank_volume_change([strong, churn], st, FILTERS, at, CURVE)
    assert [c.symbol for c in ranked] == ["STRONG", "CHURN"]
    assert ranked[1].volume_change > ranked[0].volume_change              # churn had more volume


def test_price_band_filters_the_ranking():
    """User 2026-09-28: only stocks priced 250..2500."""
    band = ScanFilters.from_config({"min_price": 250, "max_price": 2500, "min_avg_daily_volume": 500_000,
                                    "min_avg_traded_value": 5e7})
    stats = {s: daily_stats(s, daily(s, [(p, 1_000_000)] * 20), TODAY, 20)
             for s, p in (("CHEAP", 200), ("MID", 1000), ("DEAR", 3000))}
    quotes = [q("CHEAP", 2_000_000, 210), q("MID", 2_000_000, 1010), q("DEAR", 2_000_000, 3030)]
    assert [c.symbol for c in rank_volume_change(quotes, stats, band, time(12, 22), CURVE)] == ["MID"]


def test_short_ranking_keeps_only_down_movers_with_a_mirrored_score():
    at = time(12, 22)
    quotes = [q("UP", 2_000_000, 103) | {"high": 103, "low": 99},
              q("DOWN", 2_000_000, 97) | {"high": 101, "low": 97},
              q("DOWNWEAK", 2_000_000, 99.5) | {"high": 101, "low": 99}]
    st = {s: stats(s) for s in ("UP", "DOWN", "DOWNWEAK")}
    short = rank_volume_change(quotes, st, FILTERS, at, CURVE, short=True)
    assert [c.symbol for c in short] == ["DOWN", "DOWNWEAK"]          # long_only does not apply
    long_ = rank_volume_change(quotes, st, FILTERS, at, CURVE)
    assert [c.symbol for c in long_] == ["UP"]
    m_s, m_l = short[0].parts["movement"], long_[0].parts["movement"]
    assert m_s["change"] == pytest.approx(m_l["change"])             # 3% down mirrors 3% up
    assert m_s["position"] == m_l["position"] == 100.0                 # at the low / at the high
