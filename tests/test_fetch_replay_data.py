from datetime import date, datetime, time, timedelta

from scripts.fetch_replay_data import download_sessions
from src.app.sources import ReplaySource
from src.data.models import Candle, Exchange, Instrument, Segment

DAY = date(2026, 9, 25)   # a Friday


class StubAdapter:
    """Two bars per weekday; records requests. Read-only, like the real adapter."""

    def __init__(self, fail=()):
        self.requests, self.fail = [], set(fail)

    def get_historical_candles(self, req):
        self.requests.append(req)
        if req.instrument.trading_symbol in self.fail:
            raise RuntimeError("boom")
        out, d = [], req.start_time.date()
        while d <= req.end_time.date():
            if d.weekday() < 5:
                for m in (0, 1):
                    ts = datetime.combine(d, time(9, 15)) + timedelta(minutes=m)
                    out.append(Candle(req.instrument, 1, ts, 10, 11, 9, 10.5, 100))
            d += timedelta(days=1)
        return out


def inst(sym, is_index=False):
    return Instrument(sym, Exchange.NSE, Segment.CASH, is_index=is_index)


def test_download_writes_last_n_sessions_in_replay_layout(tmp_path):
    adapter = StubAdapter()
    report = download_sessions(adapter, [inst("AAA"), inst("NIFTY", True)], DAY, 3, tmp_path)
    days = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert days == ["2026-09-23", "2026-09-24", "2026-09-25"]
    assert report == {"AAA": 3, "NIFTY": 3}
    assert all(r.interval_minutes == 1 and r.end_time.date() == DAY for r in adapter.requests)
    stocks, index = ReplaySource(tmp_path, DAY).instruments()
    assert [s.trading_symbol for s in stocks] == ["AAA"] and index.is_index
    assert not (tmp_path / "DEMO").exists()


def test_download_reports_failures_without_stopping(tmp_path):
    report = download_sessions(StubAdapter(fail={"BAD"}), [inst("BAD"), inst("AAA")], DAY, 2,
                               tmp_path)
    assert report["AAA"] == 2 and report["BAD"].startswith("error: ")
