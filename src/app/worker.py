"""Worker — M6 (DECISIONS #14).

Each minute: candles → engine → `data/processed/state.json` (atomic
replace). The dashboard reads only that file. Read-only market data; this
process never places orders.

    python -m src.app.worker --replay data/demo --day 2026-09-25 --speed 60
    python -m src.app.worker            # live (needs Groww credentials)
"""

from __future__ import annotations

import argparse
import json
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from src.app.feed_watchdog import OFF, FeedConfig, FeedWatchdog
from src.app.sources import ReplaySource
from src.data.feed_store import FeedStore
from src.data.models import Instrument
from src.market.context import nifty_context
from src.market.sector import SectorConfig, load_sector_map, sector_snapshot, stock_context
from src.quantitative.in_play import InPlayConfig
from src.quantitative.microstructure import MicroConfig, symbol_feed
from src.recommendation.engine import SymbolInputs, evaluate_symbol, rank_recommendations
from src.recommendation.models import MODES
from src.recommendation.schema import SCHEMA_VERSION
from src.recommendation.scoring import EngineConfig
from src.utils.config import load_settings, load_strategy, load_universe, load_yaml

SESSION_END = time(15, 30)
DEFAULT_OUT = Path("data/processed/state.json")
LIVE_SNAPSHOTS = Path("data/research/live/snapshots")   # M14 research record (DECISIONS #24)


@dataclass
class WorkerContext:
    source: object                      # ReplaySource | LiveSource
    stocks: list
    index: Instrument | None
    engine_cfg: EngineConfig
    in_play_cfg: InPlayConfig
    liquidity_filters: dict
    stale_after_seconds: int
    source_name: str
    demo: bool
    micro_cfg: MicroConfig = field(default_factory=MicroConfig)
    preps: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    feed_store: FeedStore | None = None     # live only (M7)
    watchdog: FeedWatchdog | None = None
    sectors: dict = field(default_factory=dict)          # M8 sector map
    sector_cfg: SectorConfig = field(default_factory=SectorConfig)
    sector_indices: list = field(default_factory=list)   # Instruments with candle data
    news: object | None = None                           # NewsService, live only (M9)
    universe: object | None = None                       # DynamicUniverse, live scan (M11)
    bank_index: Instrument | None = None                 # BANK NIFTY (M12 market group)
    bank_sectors: frozenset = frozenset()                # sectors that also use BANK NIFTY


def base_context(source, stocks, index, source_name: str, demo: bool) -> WorkerContext:
    strategy, settings = load_strategy(), load_settings()
    sectors, problems = load_sector_map(load_universe().get("symbols") or [])
    ctx = WorkerContext(
        source, list(stocks), index, EngineConfig.from_strategy(strategy),
        InPlayConfig.from_dict(strategy.get("in_play", {})),
        load_universe().get("filters", {}), int(settings["candles"]["stale_after_seconds"]),
        source_name, demo, MicroConfig.from_dict(strategy.get("microstructure", {})),
    )
    ctx.sectors, ctx.sector_cfg = sectors, SectorConfig.from_dict(strategy.get("sector", {}))
    ctx.bank_sectors = frozenset(load_yaml("sectors.yaml").get("bank_nifty_sectors") or [])
    ctx.errors += [f"sectors.yaml: {p}" for p in problems]
    return ctx


def prepare(ctx: WorkerContext, day: date) -> None:
    for inst in ctx.stocks:
        try:
            ctx.preps[inst.trading_symbol] = ctx.source.prep(inst, day)
        except Exception as exc:        # one bad symbol must not stop the worker
            ctx.preps[inst.trading_symbol] = None
            ctx.errors.append(f"{inst.trading_symbol}: prep failed: {exc}")


def _stock_coverage(ctx: WorkerContext, snap) -> float | None:
    """Share of stocks with a tick within the watchdog's down window."""
    if snap is None or not ctx.stocks:
        return None
    window = ctx.watchdog.cfg.down_after_seconds
    fresh = sum(1 for i in ctx.stocks
                if (st := snap.symbols.get(i.trading_symbol)) is not None
                and st.last_tick_age_s is not None and st.last_tick_age_s <= window)
    return fresh / len(ctx.stocks)


def _feed_block(ctx: WorkerContext, snap) -> dict:
    wd = ctx.watchdog
    return {
        "status": wd.status if wd else OFF,
        "last_tick_age_s": snap.last_any_tick_age_s if snap else None,
        "restarts": wd.restarts if wd else 0,
        "subscribed": wd.feed.subscribed if wd else 0,
        "bad_payloads": snap.bad_payloads if snap else 0,
    }


def run_tick(ctx: WorkerContext, as_of: datetime, generated_at: datetime) -> dict:
    """`as_of` is the minute being scored; feed ages use the real clock
    `generated_at` (ticks arrive after the minute boundary)."""
    errors = list(ctx.errors)
    if ctx.universe is not None:
        errors += ctx.universe.refresh(ctx, as_of)    # top N by volume change (M11)
    snap = ctx.feed_store.snapshot(generated_at) if ctx.feed_store else None
    if ctx.watchdog is not None:
        ctx.watchdog.check(generated_at, snap.last_any_tick_age_s if snap else None,
                           _stock_coverage(ctx, snap))
        if ctx.watchdog.last_error:
            errors.append(f"feed: {ctx.watchdog.last_error}")
    if ctx.news is not None:
        errors += ctx.news.tick(generated_at)       # staggered; a few stocks per minute
    index_bars = []
    if ctx.index is not None:
        try:
            index_bars = ctx.source.minute_candles(ctx.index, as_of)
        except Exception as exc:
            errors.append(f"{ctx.index.trading_symbol}: {exc}")
    market = nifty_context(index_bars, as_of, ctx.stale_after_seconds,
                           ctx.engine_cfg.market_ramp_pct)
    bank_ctx = None
    if ctx.bank_index is not None:
        try:
            bank_ctx = nifty_context(ctx.source.minute_candles(ctx.bank_index, as_of), as_of,
                                     ctx.stale_after_seconds, ctx.engine_cfg.market_ramp_pct)
        except Exception as exc:            # BANK NIFTY missing → the part drops out
            errors.append(f"BANKNIFTY: {exc}")
    by_mode: dict = {m: [] for m in MODES}
    excluded, in_play, freshest = [], 0, None
    bars_by: dict = {}
    for inst in ctx.stocks:
        try:
            bars_by[inst.trading_symbol] = ctx.source.minute_candles(inst, as_of)
        except Exception as exc:
            errors.append(f"{inst.trading_symbol}: {exc}")
    sector_bars: dict = {}
    for inst in ctx.sector_indices:
        try:
            sector_bars[inst.trading_symbol] = ctx.source.minute_candles(inst, as_of)
        except Exception as exc:        # missing sector data → UNAVAILABLE, not fatal
            errors.append(f"{inst.trading_symbol}: {exc}")
    snapshot = sector_snapshot(ctx.sectors, sector_bars, bars_by,
                               market.nifty_change_pct if market.status == "available" else None,
                               as_of, ctx.stale_after_seconds, ctx.sector_cfg)
    for inst in ctx.stocks:
        sym = inst.trading_symbol
        pr = ctx.preps.get(sym)
        if sym not in bars_by:
            excluded.append({"symbol": sym, "reason": "error during evaluation"})
            continue
        try:
            feed = (symbol_feed(snap, sym, ctx.stale_after_seconds, ctx.micro_cfg)
                    if snap else None)
            sc = stock_context(sym, snapshot, bars_by[sym], ctx.sector_cfg)
            inputs = SymbolInputs(sym, bars_by[sym], pr.prep if pr else None,
                                  pr.volume_curve if pr else [], pr.liquidity if pr else None,
                                  index_bars, feed, sc,
                                  ctx.news.result(sym, generated_at) if ctx.news else None,
                                  bank_ctx if sc.get("sector") in ctx.bank_sectors else None)
            ev = evaluate_symbol(inputs, market, as_of, ctx.engine_cfg, ctx.in_play_cfg,
                                 ctx.liquidity_filters, ctx.stale_after_seconds)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            excluded.append({"symbol": sym, "reason": "error during evaluation"})
            continue
        if ev.excluded_reason:
            excluded.append({"symbol": sym, "reason": ev.excluded_reason})
            continue
        in_play += ev.is_in_play
        for mode, rec in ev.recommendations.items():
            by_mode[mode].append(rec)
            age = rec.data_quality.data_age_seconds
            if age is not None and (freshest is None or age < freshest):
                freshest = age
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "source": ctx.source_name,
        "demo": ctx.demo,
        "data_age_seconds": freshest,
        "market": asdict(market),
        "modes": {m: [r.to_dict() for r in rank_recommendations(by_mode[m])] for m in MODES},
        "excluded": excluded,
        "in_play_count": in_play,
        "universe_count": len(ctx.stocks),
        "errors": errors,
        "feed": _feed_block(ctx, snap),
        "universe": (ctx.universe.block(ctx) if ctx.universe is not None else
                     {"source": "fixed",
                      "active": [{"symbol": i.trading_symbol} for i in ctx.stocks]}),
    }


def write_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)                   # atomic: the dashboard never reads a partial file


def replay_clock(day: date):
    t, end = datetime.combine(day, time(9, 16)), datetime.combine(day, SESSION_END)
    while t <= end:
        yield t
        t += timedelta(minutes=1)


def live_clock():
    """Naive local time, assumed IST. Yields each minute after its bar closes."""
    while True:
        now = datetime.now().replace(second=0, microsecond=0)
        if now.time() > SESSION_END:
            return
        yield now
        nxt = now + timedelta(minutes=1, seconds=5)
        _time.sleep(max(0.0, (nxt - datetime.now()).total_seconds()))


def _live_context() -> WorkerContext:
    """Untested against the real Groww API (needs credentials + subscription)."""
    from src.app.sources import LiveSource
    from src.broker.groww import GrowwAdapter
    from src.broker.groww_instruments import InstrumentMaster
    from src.data.universe import resolve_universe
    from src.storage.candle_cache import IntradayCandleCache
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")   # credentials; never logged
    storage = load_settings()["storage"]
    data_dir = Path(storage["data_dir"])
    master = InstrumentMaster.load(data_dir / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    adapter.authenticate()
    uni = resolve_universe(load_universe(), lambda s, e: adapter.resolve_instrument(s, e, "CASH"))
    index = next((i for i in uni.indices if i.trading_symbol == "NIFTY"), None)
    source = LiveSource(adapter, IntradayCandleCache(data_dir / "cache" / "intraday"))
    scan_cfg = load_universe().get("scan") or {}
    scanning = bool(scan_cfg.get("enabled"))
    stocks = [] if scanning else uni.stocks      # scan: universe filled by DynamicUniverse
    ctx = base_context(source, stocks, index, "live", False)
    ctx.errors += [f"{s}: {r}" for s, r in uni.rejected.items()]
    try:
        ctx.bank_index = adapter.resolve_instrument("BANKNIFTY", "NSE", "CASH")
    except ValueError as exc:
        ctx.errors.append(f"BANKNIFTY: {exc}")
    for sec in ctx.sectors.values():
        try:
            ctx.sector_indices.append(adapter.resolve_instrument(sec["index"], "NSE", "CASH"))
        except ValueError as exc:       # sector stays UNAVAILABLE
            ctx.errors.append(f"sector index {sec['index']}: {exc}")
    from src.broker.groww_feed import LiveFeed
    feed_cfg = FeedConfig.from_dict(load_settings().get("feed", {}))
    ctx.feed_store = FeedStore()
    feed = LiveFeed(adapter.api_client, stocks, index, ctx.feed_store,
                    poll_seconds=feed_cfg.poll_seconds)
    ctx.watchdog = FeedWatchdog(feed, feed_cfg)
    ctx.news = _news_service([i.trading_symbol for i in stocks])
    if scanning:
        ctx.universe = _dynamic_universe(adapter, master, scan_cfg, data_dir, feed,
                                         ctx.feed_store)
    return ctx


def _dynamic_universe(adapter, master, scan_cfg: dict, data_dir: Path, feed, feed_store):
    """Market-wide volume scan over every NSE EQ intraday stock (M11)."""
    from src.app.dynamic_universe import DynamicUniverse
    from src.app.market_scanner import MarketScanner, ScannerConfig
    from src.quantitative.volume_scan import ActiveSet, ScanFilters, load_market_curve

    f = load_universe().get("filters", {})
    cfg = ScannerConfig.from_dict(scan_cfg)
    scanner = MarketScanner(
        adapter, master.scan_universe(),
        ScanFilters.from_config(f, bool(scan_cfg.get("long_only", True))),
        cfg, load_market_curve(), data_dir / "cache", data_dir / "scans",
        ltp_source=lambda: _feed_prices(feed_store))
    return DynamicUniverse(scanner, ActiveSet(cfg.top_n, cfg.min_stay_minutes), master.resolve,
                           date.today(), feed=feed,
                           news_aliases=load_yaml("news.yaml").get("aliases") or {},
                           short_set=ActiveSet(cfg.short_top_n, cfg.min_stay_minutes)
                           if cfg.short_top_n else None)


def _feed_prices(store) -> dict[str, float]:
    """Latest feed LTP per subscribed symbol; the scanner fetches the rest (M13)."""
    snap = store.snapshot(datetime.now())
    return {sym: st.ltp for sym, st in snap.symbols.items() if st.ltp is not None}


def _replay_universe(root: Path, day: date):
    """Whole-market replay (M16, DECISIONS #26): the day's liquid pool from
    `<root>/pools.json` (built from prior sessions only), ranked each minute
    from closed 1-min bars, same sticky top N as live."""
    from src.app.dynamic_universe import DynamicUniverse
    from src.app.market_scanner import ScannerConfig
    from src.data.models import Exchange, Segment
    from src.quantitative.volume_scan import ActiveSet, DailyStats, ScanFilters, load_market_curve
    from src.research.universe_history import ReplayScanner
    from src.storage.candle_cache import IntradayCandleCache

    uni = load_universe()
    scan_cfg = uni.get("scan") or {}
    pools = json.loads((root / "pools.json").read_text(encoding="utf-8"))
    stats = {s: DailyStats(**v) for s, v in (pools.get(day.isoformat()) or {}).items()}
    cache = IntradayCandleCache(root)
    inst = lambda s: Instrument(s, Exchange.NSE, Segment.CASH)
    scanner = ReplayScanner(day, stats, lambda s: cache.load(inst(s), day),
                            ScanFilters.from_config(uni.get("filters", {}),
                                                    bool(scan_cfg.get("long_only", True))),
                            load_market_curve())
    cfg = ScannerConfig.from_dict(scan_cfg)
    return DynamicUniverse(scanner, ActiveSet(cfg.top_n, cfg.min_stay_minutes), inst, day)


def _news_service(symbols: list[str]):
    """Google News RSS headlines + headline-context rules (M9). Live only."""
    from src.qualitative.headline_rules import NewsRules
    from src.qualitative.news_service import NewsConfig, NewsService
    from src.qualitative.news_source import GoogleNewsRSS

    raw = load_yaml("news.yaml")
    cfg = NewsConfig.from_dict(raw)
    return NewsService(GoogleNewsRSS(timeout=cfg.timeout_seconds), NewsRules.from_dict(raw),
                       cfg, raw.get("aliases") or {}, symbols)


def _long_research_view(ctx, state: dict) -> dict:
    """The research panel (M14, pre-registered) is about LONG candidates among
    the up-movers; names that are in the universe only as short-side movers
    (DECISIONS #30) are left out so the recorded population is unchanged."""
    shorts = ctx.universe.short_only() if ctx.universe is not None and \
        hasattr(ctx.universe, "short_only") else set()
    if not shorts:
        return state
    modes = {m: [r for r in recs if r["symbol"] not in shorts]
             for m, recs in (state.get("modes") or {}).items()}
    return state | {"modes": modes}


def run_loop(ctx: WorkerContext, clock, out: Path, delay: float, ticks: int | None,
             recorder=None, paper=None) -> None:
    """Tick until the clock ends; the live feed is always stopped on exit.
    `paper`: optional LivePaper simulation (DECISIONS #29), never places orders."""
    as_of = None
    try:
        for n, as_of in enumerate(clock, 1):
            state = run_tick(ctx, as_of, datetime.now())
            write_state(out, state)
            if recorder is not None:
                try:
                    recorder.record(_long_research_view(ctx, state))
                except Exception as exc:        # research must never stop the worker
                    ctx.errors.append(f"snapshots: {type(exc).__name__}: {exc}")
            if paper is not None:
                try:
                    paper.on_minute(ctx, as_of, state)
                except Exception as exc:        # the simulation must never stop the worker
                    ctx.errors.append(f"paper: {type(exc).__name__}: {exc}")
            if ticks and n >= ticks:
                break
            if delay:
                _time.sleep(delay)
    finally:
        if paper is not None:
            try:
                paper.stop((as_of or datetime.now()).date())
            except Exception as exc:
                ctx.errors.append(f"paper: {type(exc).__name__}: {exc}")
        if ctx.watchdog is not None:
            ctx.watchdog.feed.stop()
        if ctx.universe is not None and hasattr(ctx.universe.scanner, "stop"):
            ctx.universe.scanner.stop()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Recommendation worker (never places orders).")
    p.add_argument("--replay", type=Path, help="replay dir of per-day 1-min CSVs")
    p.add_argument("--day", type=date.fromisoformat, help="replay day YYYY-MM-DD")
    p.add_argument("--speed", type=float, default=60.0,
                   help="simulated minutes per real minute; 0 = no delay")
    p.add_argument("--ticks", type=int, default=None, help="stop after N ticks")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--scan-universe", action="store_true",
                   help="replay: pick the top N each minute from the day's pool (needs <replay>/pools.json)")
    p.add_argument("--snapshots", type=Path, default=None,
                   help=f"record research snapshots here (live default {LIVE_SNAPSHOTS})")
    p.add_argument("--no-snapshots", action="store_true")
    p.add_argument("--panel-minutes", type=int, default=5, help="snapshot every scored stock this often")
    p.add_argument("--paper", action="store_true",
                   help="live only: run the paper-trading simulation (config/paper.yaml); "
                        "no order ever reaches Groww")
    a = p.parse_args(argv)
    if a.replay:
        if a.day is None:
            p.error("--day is required with --replay")
        sectors, _ = load_sector_map(load_universe().get("symbols") or [])
        source = ReplaySource(a.replay, a.day,
                              {s["index"] for s in sectors.values()} | {"BANKNIFTY"})
        stocks, index = source.instruments()
        if not stocks:
            p.error(f"no candle files for {a.day} in {a.replay}")
        ctx = base_context(source, stocks, index, "replay", source.is_demo)
        ctx.sector_indices = source.sector_indices()
        ctx.bank_index = next((i for i in ctx.sector_indices
                               if i.trading_symbol == "BANKNIFTY"), None)
        if a.scan_universe:
            ctx.stocks, ctx.universe = [], _replay_universe(a.replay, a.day)
        day, clock = a.day, replay_clock(a.day)
        delay = 60.0 / a.speed if a.speed > 0 else 0.0
    else:
        ctx = _live_context()
        day, clock, delay = date.today(), live_clock(), 0.0
    prepare(ctx, day)
    if ctx.universe is not None and not a.replay:
        scanner = ctx.universe.scanner
        print(f"Volume scan: daily stats for {len(scanner.instruments)} stocks "
              "(cached per day; first run takes several minutes)...", flush=True)
        errs = scanner.prepare(day, progress=lambda n, t: print(f"  {n}/{t}", flush=True))
        ctx.errors += errs[:20]
        print(f"Volume scan pool: {len(scanner.pool)} liquid stocks; sweeping quotes.", flush=True)
        scanner.start()
    if ctx.watchdog is not None:
        ctx.watchdog.start(datetime.now())
    recorder = None
    snap_dir = a.snapshots or (None if a.replay else LIVE_SNAPSHOTS)
    if snap_dir is not None and not a.no_snapshots:
        from src.paper.journal import version_info
        from src.research.snapshots import SnapshotRecorder
        recorder = SnapshotRecorder(snap_dir, version_info() | {"source": ctx.source_name},
                                    a.panel_minutes)
    paper = None
    if not a.replay and (a.paper or load_yaml("paper.yaml").get("enabled")):
        from src.app.paper_live import build_live_papers
        paper = build_live_papers(ctx, day)    # replay days: scripts/paper_replay.py
        if ctx.universe is not None:           # open positions keep streaming prices
            ctx.universe.pinned = paper.open_symbols
        paper.start()
        for s in paper.sessions:
            held = f" (resumed; holding {', '.join(s.resumed) or 'nothing'})" \
                if s.resumed is not None else ""
            print(f"Paper trading (SIMULATION): {s.session.broker.run_id}{held}", flush=True)
    run_loop(ctx, clock, a.out, delay, a.ticks, recorder, paper)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
