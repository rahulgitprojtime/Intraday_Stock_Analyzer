import json
from datetime import date, datetime, time, timedelta

from src.app.market_scanner import MarketScanner, ScannerConfig
from src.data.models import Candle, Exchange, Instrument, Quote, Segment
from src.quantitative.volume_scan import ScanFilters
from src.utils.config import load_universe

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 12, 22)
CURVE = [(i + 1) / 375 for i in range(375)]
FILTERS = ScanFilters(20.0, 500_000, 5e7, long_only=True)


def inst(sym):
    return Instrument(sym, Exchange.NSE, Segment.CASH, exchange_token=sym, name=f"{sym} Ltd")


class FakeAdapter:
    def __init__(self, avg=None, today_vol=None, last=None, fail=(), daily_fail=(), empty=()):
        self.avg, self.today_vol, self.last, self.fail = avg or {}, today_vol or {}, last or {}, set(fail)
        self.daily_fail, self.empty = set(daily_fail), set(empty)
        self.daily_calls, self.quote_calls = [], []

    def get_daily_candles(self, instrument, start, end):
        self.daily_calls.append((instrument.trading_symbol, start, end))
        if instrument.trading_symbol in self.daily_fail:
            raise OSError("connection reset")
        if instrument.trading_symbol in self.empty:
            return []                                   # e.g. a new listing: no history yet
        v = self.avg.get(instrument.trading_symbol, 1_000_000)
        return [Candle(instrument, 1440, datetime.combine(end - timedelta(days=k), time()),
                       100, 100, 100, 100, v) for k in range(25, -1, -1)]

    def get_quote(self, instrument):
        sym = instrument.trading_symbol
        self.quote_calls.append(sym)
        if sym in self.fail:
            raise OSError("timeout")
        last = self.last.get(sym, 101.0)
        return Quote(instrument, last, 100, last, 99, 100, self.today_vol.get(sym, 500_000),
                     last - 100, last - 100)


def scanner(tmp_path, adapter, syms=("AAA", "BBB", "CCC", "THIN"), sleeps=None):
    cfg = ScannerConfig(top_n=2, min_stay_minutes=10, calls_per_minute=200, stats_sessions=20,
                        prep_calls_per_minute=120)
    return MarketScanner(adapter, [inst(s) for s in syms], FILTERS, cfg, CURVE,
                         tmp_path / "cache", tmp_path / "scans",
                         sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_config_from_universe_yaml():
    c = ScannerConfig.from_dict(load_universe()["scan"])
    assert (c.top_n, c.min_stay_minutes, c.calls_per_minute, c.stats_sessions,
            c.prep_calls_per_minute) == (25, 10, 200, 20, 150)


def test_failed_fetches_are_never_cached_and_retried_next_time(tmp_path):
    s = scanner(tmp_path, FakeAdapter(daily_fail={"BBB"}, empty={"CCC"}))
    errors = s.prepare(TODAY)
    assert errors == ["daily BBB: OSError: connection reset"]
    assert s.pool == ["AAA", "THIN"] and "CCC" not in s.stats
    again = scanner(tmp_path, FakeAdapter())
    again.prepare(TODAY)
    assert [c[0] for c in again.adapter.daily_calls] == ["BBB"]     # only the failure refetched
    assert again.pool == ["AAA", "BBB", "THIN"]                      # CCC: known "no data"


def test_prep_is_paced(tmp_path):
    sleeps = []
    s = scanner(tmp_path, FakeAdapter(), sleeps=sleeps)
    s.prepare(TODAY)
    assert len(sleeps) == 4 and all(0 < x <= 0.5 for x in sleeps)   # 120/min -> <= 0.5 s gaps


def test_prepare_builds_liquid_pool_and_caches_per_day(tmp_path):
    ad = FakeAdapter(avg={"THIN": 10_000})
    s = scanner(tmp_path, ad)
    assert s.prepare(TODAY) == []
    assert s.pool == ["AAA", "BBB", "CCC"]                           # THIN fails the floor
    assert ad.daily_calls[0][2] == TODAY - timedelta(days=1)        # stats end before today
    again = scanner(tmp_path, FakeAdapter())
    again.prepare(TODAY)
    assert again.pool == ["AAA", "BBB", "CCC"] and again.adapter.daily_calls == []   # cached


def test_sweep_round_robin_and_ranking(tmp_path):
    ad = FakeAdapter(today_vol={"AAA": 2_000_000, "BBB": 1_000_000, "CCC": 3_000_000},
                     last={"CCC": 98.0})                            # CCC below prev close
    s = scanner(tmp_path, ad, syms=("AAA", "BBB", "CCC"))
    s.prepare(TODAY)
    assert s.sweep_step(2, NOW) == []
    assert ad.quote_calls == ["AAA", "BBB"]
    s.sweep_step(2, NOW)
    assert ad.quote_calls == ["AAA", "BBB", "CCC", "AAA"]           # wraps around
    ranked = s.ranked(NOW)
    assert [c.symbol for c in ranked] == ["AAA", "BBB"]              # CCC excluded: long-only
    st = s.status()
    assert (st["pool"], st["quoted"], st["full_sweeps"]) == (3, 3, 1)


def test_quote_failures_are_reported_and_skipped(tmp_path):
    s = scanner(tmp_path, FakeAdapter(fail={"BBB"}))
    s.prepare(TODAY)
    errors = s.sweep_step(3, NOW)
    assert errors == ["scan BBB: OSError: timeout"]
    assert [c.symbol for c in s.ranked(NOW)] == ["AAA", "CCC"]


def test_scan_record_appends_top_n(tmp_path):
    ad = FakeAdapter(today_vol={"AAA": 2_000_000, "BBB": 1_000_000})
    s = scanner(tmp_path, ad)
    s.prepare(TODAY)
    s.sweep_step(3, NOW)
    s.record(NOW, ["AAA", "BBB"])
    rows = [json.loads(x) for x in (tmp_path / "scans" / "2026-09-28.jsonl").read_text().splitlines()]
    assert rows[0]["at"] == NOW.isoformat() and rows[0]["active"] == ["AAA", "BBB"]
    assert [r["symbol"] for r in rows[0]["top"]][:2] == ["AAA", "BBB"]
