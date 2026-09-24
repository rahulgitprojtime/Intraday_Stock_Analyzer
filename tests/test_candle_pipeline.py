from datetime import date, datetime, timedelta

import pytest

from src.data.candles import aggregate_daily, is_stale, resample
from src.data.models import Candle, Exchange, Instrument, Segment
from src.data.prep_builder import build_prep
from src.storage.candle_cache import IntradayCandleCache

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)


def m(ts, o=100, h=101, l=99, c=100, v=10):
    return Candle(INST, 1, ts, o, h, l, c, v)


def session(d, n=375, price=100.0, v=10):
    start = datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15)
    return [m(start + timedelta(minutes=i), price, price + 1, price - 1, price, v)
            for i in range(n)]


# -- aggregate_daily ---------------------------------------------------

def test_aggregate_daily_ohlcv_per_date():
    d1 = session(date(2026, 9, 1), n=3)
    d1[1] = m(d1[1].timestamp, 100, 110, 99, 105, 10)
    d1[2] = m(d1[2].timestamp, 105, 106, 90, 102, 10)
    days = aggregate_daily(d1 + session(date(2026, 9, 2), n=2))
    assert len(days) == 2
    first = days[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (100, 110, 90, 102, 30)
    assert first.timeframe_minutes == 1440
    assert first.timestamp.date() == date(2026, 9, 1)


# -- resample ------------------------------------------------------------

def test_resample_buckets_align_to_session_open():
    bars = resample(session(date(2026, 9, 1), n=12), 5)
    assert [b.timestamp.strftime("%H:%M") for b in bars] == ["09:15", "09:20", "09:25"]
    assert bars[0].volume == 50 and bars[2].volume == 20
    assert all(b.timeframe_minutes == 5 for b in bars)


def test_resample_marks_forming_bucket_incomplete():
    minutes = session(date(2026, 9, 1), n=7)          # 09:15..09:21
    now = datetime(2026, 9, 1, 9, 22)
    bars = resample(minutes, 5, now=now)
    assert bars[0].is_complete is True                # 09:15-09:20 closed
    assert bars[1].is_complete is False               # 09:20-09:25 forming


def test_resample_ohlc():
    d = date(2026, 9, 1)
    base = datetime(2026, 9, 1, 9, 15)
    minutes = [m(base, 10, 12, 9, 11), m(base + timedelta(minutes=1), 11, 15, 10, 14),
               m(base + timedelta(minutes=2), 14, 14, 8, 9)]
    (bar,) = resample(minutes, 3)
    assert (bar.open, bar.high, bar.low, bar.close) == (10, 15, 8, 9)
    assert bar.timestamp.date() == d


def test_is_stale():
    last = datetime(2026, 9, 1, 10, 0)
    assert not is_stale(last, datetime(2026, 9, 1, 10, 1, 30), max_age_seconds=120)
    assert is_stale(last, datetime(2026, 9, 1, 10, 3), max_age_seconds=120)
    assert is_stale(None, datetime(2026, 9, 1, 10, 3))


# -- build_prep ----------------------------------------------------------

class FakeAdapter:
    """Duck-typed BrokerAdapter: serves canned 1-min candles in range."""

    def __init__(self, candles):
        self.candles = candles
        self.requests = []

    def get_historical_candles(self, request):
        self.requests.append(request)
        return [c for c in self.candles if request.start_time <= c.timestamp <= request.end_time]


def test_build_prep_uses_only_sessions_before_today():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(20)]
    candles = []
    for i, d in enumerate(days):
        candles += session(d, price=100 + i)
    today = days[-1]
    adapter = FakeAdapter(candles)

    result = build_prep(adapter, INST, today)

    assert len(adapter.requests) == 1
    req = adapter.requests[0]
    assert req.interval_minutes == 1 and req.end_time < datetime.combine(today, datetime.min.time())
    assert result.sessions == 19
    assert result.prep.prev_close == pytest.approx(100 + 18)  # day before today
    assert result.prep.atr is not None
    assert len(result.volume_curve) == 375
    assert result.volume_curve[-1] == pytest.approx(3750)


def test_build_prep_returns_none_without_history():
    assert build_prep(FakeAdapter([]), INST, date(2026, 9, 20)) is None


# -- IntradayCandleCache ------------------------------------------------

def test_cache_refresh_is_incremental_and_dedupes(tmp_path):
    d = date(2026, 9, 1)
    all_minutes = session(d, n=10)
    adapter = FakeAdapter(all_minutes[:5])
    cache = IntradayCandleCache(tmp_path)

    now = datetime(2026, 9, 1, 9, 20, 30)
    got = cache.refresh(adapter, INST, now)
    assert len(got) == 5
    assert adapter.requests[0].start_time == datetime(2026, 9, 1, 9, 15)

    # last minute re-fetched (it may have been forming) plus new ones
    adapter.candles = all_minutes[:10]
    adapter.requests.clear()
    now = datetime(2026, 9, 1, 9, 24, 30)
    got = cache.refresh(adapter, INST, now)
    assert adapter.requests[0].start_time == all_minutes[4].timestamp
    assert [c.timestamp for c in got] == [c.timestamp for c in all_minutes]
    assert got[-1].is_complete is False       # 09:24 bar still forming at 09:24:30
    assert got[-2].is_complete is True

    # persisted: a fresh cache instance reads the same bars from disk
    reloaded = IntradayCandleCache(tmp_path).load(INST, d)
    assert [(c.timestamp, c.close, c.volume) for c in reloaded] == \
        [(c.timestamp, c.close, c.volume) for c in got]
