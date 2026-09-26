from datetime import datetime, time, timedelta

from src.app.sources import LiveSource, ReplaySource
from src.data.models import Candle, Exchange, HistoricalCandleRequest, Instrument, Segment
from src.storage.candle_cache import IntradayCandleCache
from tests.fakes.replay_fixture import REPLAY_DAY, write_replay_fixture

AAA = Instrument("AAA", Exchange.NSE, Segment.CASH)


def at(h, m, day=REPLAY_DAY):
    return datetime.combine(day, time(h, m))


def test_replay_instruments_split_index(tmp_path):
    write_replay_fixture(tmp_path)
    stocks, index = ReplaySource(tmp_path, REPLAY_DAY).instruments()
    assert [s.trading_symbol for s in stocks] == ["AAA", "BBB"]
    assert index.trading_symbol == "NIFTY" and index.is_index


def test_replay_never_returns_bars_after_clock(tmp_path):
    write_replay_fixture(tmp_path)
    bars = ReplaySource(tmp_path, REPLAY_DAY).minute_candles(AAA, at(9, 18))
    assert [b.timestamp for b in bars] == [at(9, 15), at(9, 16), at(9, 17)]


def test_replay_prep_uses_only_prior_sessions(tmp_path):
    write_replay_fixture(tmp_path)
    src = ReplaySource(tmp_path, REPLAY_DAY)
    assert src.prep(AAA, REPLAY_DAY).sessions == 2
    req = HistoricalCandleRequest(AAA, at(9, 15, REPLAY_DAY - timedelta(days=2)), at(15, 30), 1)
    assert all(c.timestamp.date() < REPLAY_DAY for c in src.get_historical_candles(req))


def test_replay_is_deterministic(tmp_path):
    write_replay_fixture(tmp_path)
    a = ReplaySource(tmp_path, REPLAY_DAY).minute_candles(AAA, at(9, 40))
    b = ReplaySource(tmp_path, REPLAY_DAY).minute_candles(AAA, at(9, 40))
    assert a == b and len(a) == 25


def test_demo_marker(tmp_path):
    write_replay_fixture(tmp_path)
    assert not ReplaySource(tmp_path, REPLAY_DAY).is_demo
    (tmp_path / "DEMO").write_text("x")
    assert ReplaySource(tmp_path, REPLAY_DAY).is_demo


class ReadOnlyAdapter:
    """Defines only the read method the source may use; any other call
    (orders included) would raise AttributeError."""

    def __init__(self):
        self.calls = []

    def get_historical_candles(self, request):
        self.calls.append("get_historical_candles")
        start = request.start_time.replace(hour=9, minute=15)
        return [Candle(request.instrument, 1, start + timedelta(minutes=i), 1, 1, 1, 1, 10)
                for i in range(3)]


def test_live_source_uses_only_read_calls(tmp_path):
    adapter = ReadOnlyAdapter()
    live = LiveSource(adapter, IntradayCandleCache(tmp_path))
    bars = live.minute_candles(AAA, at(9, 17, datetime(2026, 9, 25).date()))
    assert [b.timestamp for b in bars] == [at(9, 15), at(9, 16)]   # 09:17 still forming
    assert set(adapter.calls) == {"get_historical_candles"}
