from datetime import datetime, time

from src.app.dynamic_universe import DynamicUniverse
from src.app.sources import ReplaySource
from src.app.worker import base_context, prepare, run_tick
from src.data.models import Exchange, Instrument, Segment
from src.quantitative.volume_scan import ActiveSet, ScanCandidate
from src.recommendation.schema import validate_state
from tests.fakes.replay_fixture import REPLAY_DAY, write_replay_fixture

AS_OF = datetime.combine(REPLAY_DAY, time(9, 30))


class FakeScanner:
    def __init__(self, ranking):
        self.ranking, self.records = ranking, []

    def ranked(self, now):
        return [ScanCandidate(s, v, 1.5, 100.0, 1000) for s, v in self.ranking]

    def record(self, now, active):
        self.records.append((now, active))

    def status(self):
        return {"pool": 700, "quoted": 650, "full_sweeps": 3, "universe": 1643, "stats": 1600}


class FakeFeed:
    def __init__(self):
        self.sets = []

    def set_stocks(self, stocks):
        self.sets.append([s.trading_symbol for s in stocks])


class FakeNews:
    def __init__(self):
        self.added = {}

    def add_symbols(self, aliases):
        self.added |= aliases

    def tick(self, now):
        return []

    def result(self, symbol, now):
        return None


def ctx_with(tmp_path, ranking, top_n=1):
    write_replay_fixture(tmp_path)                    # AAA, BBB + NIFTY
    src = ReplaySource(tmp_path, REPLAY_DAY)
    _, index = src.instruments()
    ctx = base_context(src, [], index, "live", False)
    prepare(ctx, REPLAY_DAY)
    feed, news, scanner = FakeFeed(), FakeNews(), FakeScanner(ranking)
    resolve = lambda s: Instrument(s, Exchange.NSE, Segment.CASH, exchange_token=s,  # noqa: E731
                                   name=f"{s} Industries")
    ctx.universe = DynamicUniverse(scanner, ActiveSet(top_n, 10), resolve, REPLAY_DAY,
                                   feed=feed, news_aliases={"AAA": ["Aaa Configured"]})
    ctx.news = news
    return ctx, scanner, feed, news


def test_first_refresh_builds_universe_preps_and_subscribes(tmp_path):
    ctx, scanner, feed, news = ctx_with(tmp_path, [("AAA", 4.0), ("BBB", 2.0)], top_n=2)
    state = run_tick(ctx, AS_OF, AS_OF)
    assert [i.trading_symbol for i in ctx.stocks] == ["AAA", "BBB"]
    assert set(ctx.preps) == {"AAA", "BBB"} and all(ctx.preps.values())
    assert feed.sets == [["AAA", "BBB"]]
    assert news.added == {"AAA": ["Aaa Configured"], "BBB": ["BBB Industries"]}
    assert scanner.records == [(AS_OF, ["AAA", "BBB"])]
    assert validate_state(state) == [] and state["universe_count"] == 2
    u = state["universe"]
    assert u["source"] == "volume_scan" and u["pool"] == 700 and u["top_n"] == 2
    assert [(a["symbol"], a["volume_change"]) for a in u["active"]] == [("AAA", 4.0), ("BBB", 2.0)]
    assert all("scan_score" in a for a in u["active"])


def test_unchanged_universe_does_not_resubscribe(tmp_path):
    ctx, _, feed, _ = ctx_with(tmp_path, [("AAA", 4.0)])
    run_tick(ctx, AS_OF, AS_OF)
    run_tick(ctx, AS_OF.replace(minute=31), AS_OF.replace(minute=31))
    assert feed.sets == [["AAA"]]


def test_empty_scan_gives_empty_universe_not_a_crash(tmp_path):
    ctx, _, feed, _ = ctx_with(tmp_path, [])
    state = run_tick(ctx, AS_OF, AS_OF)
    assert state["universe_count"] == 0 and state["modes"] == {"SCALP": [], "DAY": []}
    assert state["universe"]["active"] == [] and validate_state(state) == []


def test_fixed_universe_block_for_replay(tmp_path):
    write_replay_fixture(tmp_path)
    src = ReplaySource(tmp_path, REPLAY_DAY)
    stocks, index = src.instruments()
    ctx = base_context(src, stocks, index, "replay", False)
    prepare(ctx, REPLAY_DAY)
    u = run_tick(ctx, AS_OF, AS_OF)["universe"]
    assert u == {"source": "fixed", "active": [{"symbol": "AAA"}, {"symbol": "BBB"}]}


def test_run_loop_stops_scanner_even_on_error(tmp_path):
    from src.app.worker import run_loop
    ctx, scanner, _, _ = ctx_with(tmp_path, [("AAA", 4.0)])
    scanner.stopped = 0
    scanner.stop = lambda: setattr(scanner, "stopped", scanner.stopped + 1)

    def clock():
        yield AS_OF
        raise RuntimeError("boom")

    try:
        run_loop(ctx, clock(), tmp_path / "s.json", delay=0.0, ticks=None)
    except RuntimeError:
        pass
    assert scanner.stopped == 1


def test_universe_size_follows_scanner_capacity_and_flags_short_names():
    from types import SimpleNamespace

    from src.app.worker import _long_research_view

    cand = lambda s, d: SimpleNamespace(symbol=s, direction=d)        # noqa: E731

    class Scanner:
        cap = 2

        def ranked(self, now):
            return [cand("DNA", "SHORT"), cand("UPA", "LONG"), cand("UPB", "LONG")]

        def capacity(self):
            return self.cap

        def record(self, now, active):
            self.active = active

    inst = lambda s: SimpleNamespace(trading_symbol=s, name=s)        # noqa: E731
    sc = Scanner()
    uni = DynamicUniverse(sc, ActiveSet(99, 0), inst, None)
    ctx = SimpleNamespace(stocks=[], preps={}, news=None,
                          source=SimpleNamespace(prep=lambda i, d: None), universe=uni)
    uni.refresh(ctx, datetime(2026, 10, 9, 10, 0))
    assert [i.trading_symbol for i in ctx.stocks] == ["DNA", "UPA"]   # capacity, not a top-N
    assert uni.short_only() == {"DNA"}
    sc.cap = 3
    uni.refresh(ctx, datetime(2026, 10, 9, 10, 1))
    assert len(ctx.stocks) == 3
    state = {"modes": {"DAY": [{"symbol": "UPA"}, {"symbol": "DNA"}]}, "as_of": "x"}
    assert [r["symbol"] for r in _long_research_view(ctx, state)["modes"]["DAY"]] == ["UPA"]
