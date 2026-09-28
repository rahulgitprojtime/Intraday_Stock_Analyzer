import json
from datetime import datetime, time, timedelta

import pytest

from src.app.sources import ReplaySource
from src.app.feed_watchdog import FeedConfig, FeedWatchdog
from src.app.worker import base_context, main, prepare, run_loop, run_tick
from src.data.feed_store import FeedStore
from src.data.models import DepthSnapshot
from src.recommendation.schema import validate_state
from tests.fakes.replay_fixture import REPLAY_DAY, write_replay_fixture

AS_OF = datetime.combine(REPLAY_DAY, time(9, 30))
GEN = datetime(2026, 9, 25, 9, 30, 5)


def context(root, **fixture):
    write_replay_fixture(root, **fixture)
    src = ReplaySource(root, REPLAY_DAY)
    stocks, index = src.instruments()
    ctx = base_context(src, stocks, index, "replay", src.is_demo)
    prepare(ctx, REPLAY_DAY)
    return ctx


def test_tick_writes_schema_valid_ranked_state(tmp_path):
    state = run_tick(context(tmp_path), AS_OF, GEN)
    assert validate_state(state) == []
    assert state["schema_version"] == 6 and state["source"] == "replay"
    assert state["feed"] == {"status": "OFF", "last_tick_age_s": None, "restarts": 0,
                             "subscribed": 0, "bad_payloads": 0}
    assert state["universe_count"] == 2 and state["errors"] == []
    for mode in ("SCALP", "DAY"):
        recs = state["modes"][mode]
        ranked = [r for r in recs if r["eligible_for_top_n"]]
        assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
        avoid = [r for r in recs if not r["eligible_for_top_n"]]
        assert all(r["rank"] is None and r["category"] == "AVOID" and r["exclusion_reasons"]
                   for r in avoid)                        # kept for transparency
        assert all(r["data_quality"]["market_data_timestamp"] < state["as_of"] for r in recs)
    json.dumps(state)                                    # JSON-serializable


def test_prep_failure_is_reported_not_fatal(tmp_path):
    ctx = context(tmp_path)
    original = ctx.source.prep

    def flaky(inst, day):
        if inst.trading_symbol == "AAA":
            raise RuntimeError("boom")
        return original(inst, day)

    ctx.source.prep = flaky
    ctx.errors.clear()
    prepare(ctx, REPLAY_DAY)
    state = run_tick(ctx, AS_OF, GEN)
    assert any("AAA" in e and "boom" in e for e in state["errors"])
    assert [x["symbol"] for x in state["excluded"]] == ["AAA"]
    assert [r["symbol"] for r in state["modes"]["DAY"]] == ["BBB"]


def test_missing_index_marks_market_unavailable(tmp_path):
    state = run_tick(context(tmp_path, with_index=False), AS_OF, GEN)
    assert state["market"]["status"] == "unavailable"
    rec = state["modes"]["SCALP"][0]
    assert "index" in rec["data_quality"]["missing_inputs"]


def test_all_excluded_is_still_valid_state(tmp_path):
    late = datetime.combine(REPLAY_DAY, time(10, 30))    # fixture bars end 09:44 → stale
    state = run_tick(context(tmp_path), late, GEN)
    assert validate_state(state) == []
    assert state["modes"] == {"SCALP": [], "DAY": []} and len(state["excluded"]) == 2


def test_validate_state_rejects_unknown_version():
    assert "schema_version" in validate_state({"schema_version": 2})[0]


def run_cli(root, out, ticks="3"):
    return main(["--replay", str(root), "--day", REPLAY_DAY.isoformat(), "--speed", "0",
                 "--ticks", ticks, "--out", str(out)])


def test_cli_replay_writes_state_and_is_deterministic(tmp_path):
    write_replay_fixture(tmp_path / "r")
    assert run_cli(tmp_path / "r", tmp_path / "a.json") == 0
    assert run_cli(tmp_path / "r", tmp_path / "b.json") == 0
    a = json.loads((tmp_path / "a.json").read_text())
    b = json.loads((tmp_path / "b.json").read_text())
    assert a["as_of"] == datetime.combine(REPLAY_DAY, time(9, 18)).isoformat()
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_cli_rejects_empty_replay_day(tmp_path):
    with pytest.raises(SystemExit):
        run_cli(tmp_path, tmp_path / "s.json")


class StubFeed:
    subscribed = 5

    def __init__(self):
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def restart(self):
        pass

    def stop(self):
        self.stopped += 1


def live_like(tmp_path):
    """Replay candles + a live FeedStore/watchdog, as the live worker wires them."""
    ctx = context(tmp_path)
    ctx.feed_store, ctx.watchdog = FeedStore(), FeedWatchdog(StubFeed(), FeedConfig())
    ctx.watchdog.start(GEN - timedelta(minutes=5))
    return ctx


def test_live_feed_reaches_scalp_and_feed_block(tmp_path):
    ctx = live_like(tmp_path)
    for i in range(30):                                   # ticks up to GEN, after as_of
        ctx.feed_store.count_tick("AAA", GEN - timedelta(seconds=i))
    ctx.feed_store.put_depth(DepthSnapshot("AAA", GEN, 99.95, 10, 100.05, 10, 600, 200), GEN)
    state = run_tick(ctx, AS_OF, GEN)
    assert validate_state(state) == []
    assert state["feed"] == {"status": "LIVE", "last_tick_age_s": 0.0, "restarts": 0,
                             "subscribed": 5, "bad_payloads": 0}
    recs = {r["symbol"]: r for r in state["modes"]["SCALP"]}
    micro = recs["AAA"]["quantitative"]["microstructure"]
    assert micro["spread_pct"] is not None and micro["last_tick_age_s"] == 0.0
    assert recs["BBB"]["quantitative"]["microstructure"] is None     # no ticks for BBB


def test_silent_feed_reports_down(tmp_path):
    ctx = live_like(tmp_path)
    state = run_tick(ctx, AS_OF, GEN)
    assert state["feed"]["status"] == "DOWN" and state["feed"]["restarts"] == 1


def test_run_loop_stops_feed_even_on_error(tmp_path):
    ctx = live_like(tmp_path)

    def clock():
        yield AS_OF
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_loop(ctx, clock(), tmp_path / "s.json", delay=0.0, ticks=None)
    assert ctx.watchdog.feed.stopped == 1 and (tmp_path / "s.json").exists()


def test_worker_passes_stock_coverage_to_watchdog(tmp_path):
    ctx = live_like(tmp_path)
    seen = {}
    real = ctx.watchdog.check
    ctx.watchdog.check = lambda now, age, stock_coverage=None: (
        seen.update(age=age, cov=stock_coverage) or real(now, age, stock_coverage))
    ctx.feed_store.count_tick("NIFTY", GEN)
    ctx.feed_store.count_tick("AAA", GEN - timedelta(seconds=5))
    ctx.feed_store.count_tick("BBB", GEN - timedelta(seconds=300))     # silent stock
    run_tick(ctx, AS_OF, GEN)
    assert seen == {"age": 0.0, "cov": 0.5}


def test_sector_context_reaches_recommendations(tmp_path):
    write_replay_fixture(tmp_path, symbols=("AAA", "BBB", "SECIDX"))
    src = ReplaySource(tmp_path, REPLAY_DAY, index_symbols={"SECIDX"})
    stocks, index = src.instruments()
    ctx = base_context(src, stocks, index, "replay", False)
    ctx.sectors = {"S": {"index": "SECIDX", "members": ["AAA", "BBB"]}}
    ctx.sector_indices = src.sector_indices()
    prepare(ctx, REPLAY_DAY)
    state = run_tick(ctx, AS_OF, GEN)
    assert validate_state(state) == [] and state["universe_count"] == 2
    for rec in state["modes"]["DAY"]:
        sc = rec["sector_context"]
        assert sc["status"] == "available" and sc["index"] == "SECIDX"
        assert sc["verdict"] == "CONFIRMED" and sc["peers_total"] == 0     # 1 peer → index only
        assert [c["check"] for c in rec["prerequisites"]][3] == "sector"
        assert rec["prerequisites_summary"].startswith("Checked: ")


def test_missing_sector_index_data_is_unavailable_not_error(tmp_path):
    ctx = context(tmp_path)                        # default map; no sector index files
    state = run_tick(ctx, AS_OF, GEN)
    assert state["errors"] == []
    assert all(r["sector_context"]["verdict"] == "UNAVAILABLE" for r in state["modes"]["DAY"])


class StubNews:
    def __init__(self):
        self.ticks = []

    def tick(self, now):
        self.ticks.append(now)
        return ["news BBB: OSError: timed out"]

    def result(self, symbol, now):
        if symbol != "AAA":
            return None
        return {"status": "available", "verdict": "NO_RELEVANT_INFORMATION", "items": [],
                "lookback_hours": 18}


def test_news_service_is_ticked_with_real_clock_and_results_flow_through(tmp_path):
    ctx = context(tmp_path)
    ctx.news = StubNews()
    state = run_tick(ctx, AS_OF, GEN)
    assert ctx.news.ticks == [GEN] and "news BBB: OSError: timed out" in state["errors"]
    recs = {r["symbol"]: r for r in state["modes"]["DAY"]}
    assert recs["AAA"]["qualitative"]["verdict"] == "NO_RELEVANT_INFORMATION"
    assert recs["BBB"]["qualitative"] == {"status": "unavailable"}
    news = {r["symbol"]: [c for c in r["prerequisites"] if c["check"] == "news"][0]["status"]
            for r in state["modes"]["DAY"]}
    assert news == {"AAA": "NA", "BBB": "NOT_CHECKED"}
