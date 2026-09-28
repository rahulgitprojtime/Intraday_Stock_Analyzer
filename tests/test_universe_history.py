from datetime import date, datetime, time, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.quantitative.volume_scan import DailyStats, ScanFilters
from src.research.universe_history import (
    ReplayScanner,
    build_pools,
    load_daily,
    save_daily,
    trading_days,
)

BAND = ScanFilters.from_config({"min_price": 250, "max_price": 2500,
                                "min_avg_daily_volume": 500_000, "min_avg_traded_value": 5e7})
CURVE = [(i + 1) / 375 for i in range(375)]
DAY = date(2026, 9, 29)


def inst(s):
    return Instrument(s, Exchange.NSE, Segment.CASH)


def daily(sym, rows, end):
    """rows: [(close, volume)] for consecutive calendar days ending at `end`."""
    out, d = [], end - timedelta(days=len(rows) - 1)
    for c, v in rows:
        out.append(Candle(inst(sym), 1440, datetime.combine(d, time()), None, c, c, c, v))
        d += timedelta(days=1)
    return out


def test_daily_csv_round_trip_keeps_null_open(tmp_path):
    bars = daily("AAA", [(300.0, 1_000_000)] * 3, DAY)
    save_daily(tmp_path, "AAA", bars)
    back = load_daily(tmp_path, "AAA")
    assert [(b.timestamp, b.open, b.close, b.volume) for b in back] == \
           [(b.timestamp, None, 300.0, 1_000_000) for b in bars]


def test_trading_days_are_dates_most_symbols_traded():
    d = {"A": daily("A", [(300, 1)] * 5, DAY), "B": daily("B", [(300, 1)] * 5, DAY),
         "C": daily("C", [(300, 1)], DAY - timedelta(days=10))}
    assert trading_days(d) == [DAY - timedelta(days=i) for i in range(4, -1, -1)]


def test_pools_use_only_prior_sessions_and_the_price_band():
    end = DAY
    d = {"MID": daily("MID", [(1000, 1_000_000)] * 20 + [(9999, 1)], end),        # today's bar ignored
         "CHEAP": daily("CHEAP", [(200, 5_000_000)] * 21, end),
         "DEAR": daily("DEAR", [(3000, 5_000_000)] * 21, end),
         "THIN": daily("THIN", [(1000, 10_000)] * 21, end),
         "JUMP": daily("JUMP", [(240, 1_000_000)] * 20 + [(300, 1_000_000)], end)}  # enters next day
    pools = build_pools(d, [end, end + timedelta(days=1)], BAND, sessions=20)
    assert list(pools[end.isoformat()]) == ["MID"]
    assert pools[end.isoformat()]["MID"]["prev_close"] == 1000
    assert "JUMP" in pools[(end + timedelta(days=1)).isoformat()]


def minute_bars(sym, closes, vols, start=datetime(2026, 9, 29, 9, 15)):
    out = []
    for i, (c, v) in enumerate(zip(closes, vols)):
        out.append(Candle(inst(sym), 1, start + timedelta(minutes=i), c, c + 0.5, c - 0.5, c, v))
    return out


def stats(sym, prev_close, avg_volume=1_000_000):
    return DailyStats(sym, avg_volume, avg_volume * prev_close, prev_close, 20, prev_close * 0.02)


def test_replay_scanner_ranks_from_closed_bars_only():
    bars = {"HOT": minute_bars("HOT", [1010] * 5 + [1100] * 5, [50_000] * 5 + [900_000] * 5),
            "WARM": minute_bars("WARM", [1005] * 10, [20_000] * 10)}
    sc = ReplayScanner(DAY, {"HOT": stats("HOT", 1000), "WARM": stats("WARM", 1000)},
                       lambda s: bars[s], BAND, CURVE)
    t = datetime(2026, 9, 29, 9, 20)                   # bars 09:15..09:19 closed
    early = {c.symbol: c for c in sc.ranked(t)}
    assert early["HOT"].last_price == 1010 and early["HOT"].volume == 250_000
    late = sc.ranked(datetime(2026, 9, 29, 9, 25))
    assert [c.symbol for c in late][0] == "HOT" and late[0].last_price == 1100
    assert late[0].parts["volume"]["acceleration"] is not None     # previous minute known


def test_replay_scanner_respects_band_and_long_only():
    bars = {"DOWN": minute_bars("DOWN", [990] * 5, [500_000] * 5),
            "DEAR": minute_bars("DEAR", [2600] * 5, [500_000] * 5)}
    sc = ReplayScanner(DAY, {"DOWN": stats("DOWN", 1000), "DEAR": stats("DEAR", 2400)},
                       lambda s: bars[s], BAND, CURVE)
    assert sc.ranked(datetime(2026, 9, 29, 9, 20)) == []
    assert sc.status()["pool"] == 2
