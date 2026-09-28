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
            c.prep_calls_per_minute) == (50, 10, 200, 20, 150)


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


def test_sweep_keeps_quote_shape_and_previous_volume_for_acceleration(tmp_path):
    ad = FakeAdapter(today_vol={"AAA": 1_000_000})
    s = scanner(tmp_path, ad, syms=("AAA",))
    s.prepare(TODAY)
    s.sweep_step(1, NOW)
    ad.today_vol["AAA"] = 1_300_000
    s.sweep_step(1, NOW + timedelta(minutes=3))
    q = s._quotes["AAA"]
    assert (q["volume"], q["prev_volume"], q["prev_at"]) == (1_300_000, 1_000_000, NOW)
    assert {"open", "high", "low", "average_price", "at"} <= set(q)
    c = s.ranked(NOW + timedelta(minutes=3))[0]
    assert c.parts["volume"]["acceleration"] is not None


from src.data.models import OHLC  # noqa: E402


class BatchAdapter(FakeAdapter):
    """Adds the batch calls the fast cycle uses (verified shapes: get_ohlc close =
    previous close; both take up to 50 instruments per call)."""

    def __init__(self, moves, **kw):
        super().__init__(**kw)
        self.moves = moves                     # symbol -> (last, day_high, day_low)
        self.ohlc_calls = self.ltp_calls = 0

    def get_ohlc(self, instruments):
        self.ohlc_calls += 1
        return {i.trading_symbol: OHLC(i, 100.0, self.moves[i.trading_symbol][1],
                                       self.moves[i.trading_symbol][2], 100.0)
                for i in instruments}

    def get_ltp(self, instruments):
        self.ltp_calls += 1
        return {i.trading_symbol: self.moves[i.trading_symbol][0] for i in instruments}

    def get_quote(self, instrument):
        sym = instrument.trading_symbol
        self.quote_calls.append(sym)
        last, high, low = self.moves[sym]
        return Quote(instrument, last, 100, high, low, 100, 1_000_000, last - 100, last - 100)


def fast_scanner(tmp_path, adapter, syms, movers=2):
    cfg = ScannerConfig(top_n=2, min_stay_minutes=10, calls_per_minute=200, stats_sessions=20,
                        prep_calls_per_minute=120, movers_per_cycle=movers, quote_workers=3,
                        max_calls_per_second=1000, quote_max_age_seconds=180)
    return MarketScanner(adapter, [inst(s) for s in syms], FILTERS, cfg, CURVE,
                         tmp_path / "cache", tmp_path / "scans", sleep=lambda s: None)


def test_cycle_quotes_only_the_top_movers(tmp_path):
    moves = {"UP3": (103.0, 103.2, 99.8), "UP1": (101.0, 101.5, 99.5),
             "FLAT": (100.1, 100.6, 99.4), "DOWN": (98.0, 100.2, 97.9)}
    ad = BatchAdapter(moves)
    s = fast_scanner(tmp_path, ad, list(moves), movers=2)
    s.prepare(TODAY)
    assert s.cycle(NOW, ltp={k: v[0] for k, v in moves.items()}) == []
    assert ad.ohlc_calls == 1 and ad.ltp_calls == 0          # feed prices used, no LTP call
    assert sorted(ad.quote_calls) == ["UP1", "UP3"]          # only the 2 strongest movers
    assert [c.symbol for c in s.ranked(NOW)] == ["UP3", "UP1"]


def test_cycle_falls_back_to_batch_ltp_without_feed(tmp_path):
    moves = {"UP3": (103.0, 103.2, 99.8), "UP1": (101.0, 101.5, 99.5)}
    ad = BatchAdapter(moves)
    s = fast_scanner(tmp_path, ad, list(moves))
    s.prepare(TODAY)
    s.cycle(NOW, ltp=None)
    assert ad.ltp_calls == 1 and len(ad.quote_calls) == 2


def test_stale_quotes_drop_out_of_the_ranking(tmp_path):
    moves = {"UP3": (103.0, 103.2, 99.8), "UP1": (101.0, 101.5, 99.5)}
    ad = BatchAdapter(moves)
    s = fast_scanner(tmp_path, ad, list(moves))
    s.prepare(TODAY)
    s.cycle(NOW, ltp={k: v[0] for k, v in moves.items()})
    assert len(s.ranked(NOW + timedelta(minutes=2))) == 2
    assert s.ranked(NOW + timedelta(minutes=4)) == []        # older than 180 s


def test_cycle_fetches_ltp_for_pool_stocks_the_feed_does_not_cover(tmp_path):
    """The feed subscribes only to the active set; the rest of the pool still
    needs prices, so missing symbols come from one batch LTP call."""
    moves = {"UP3": (103.0, 103.2, 99.8), "UP1": (101.0, 101.5, 99.5)}
    ad = BatchAdapter(moves)
    seen = []
    orig = ad.get_ltp
    ad.get_ltp = lambda insts: (seen.extend(i.trading_symbol for i in insts), orig(insts))[1]
    s = fast_scanner(tmp_path, ad, list(moves))
    s.prepare(TODAY)
    s.cycle(NOW, ltp={"UP3": 103.0})
    assert seen == ["UP1"] and sorted(ad.quote_calls) == ["UP1", "UP3"]


def test_worker_feed_prices_skip_symbols_without_ltp():
    from src.app.worker import _feed_prices
    from src.data.feed_store import FeedStore
    store = FeedStore()
    store.count_tick("NOLTP", NOW)
    assert _feed_prices(store) == {}
