"""Worker — M6 (spec §3, §17, §22-25).

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

from src.app.sources import ReplaySource
from src.data.models import Instrument
from src.market.context import nifty_context
from src.quantitative.in_play import InPlayConfig
from src.recommendation.engine import SymbolInputs, evaluate_symbol, rank_recommendations
from src.recommendation.models import MODES
from src.recommendation.schema import SCHEMA_VERSION
from src.recommendation.scoring import EngineConfig
from src.utils.config import load_settings, load_strategy, load_universe

SESSION_END = time(15, 30)
DEFAULT_OUT = Path("data/processed/state.json")


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
    preps: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def base_context(source, stocks, index, source_name: str, demo: bool) -> WorkerContext:
    strategy, settings = load_strategy(), load_settings()
    return WorkerContext(
        source, list(stocks), index, EngineConfig.from_strategy(strategy),
        InPlayConfig.from_dict(strategy.get("in_play", {})),
        load_universe().get("filters", {}), int(settings["candles"]["stale_after_seconds"]),
        source_name, demo,
    )


def prepare(ctx: WorkerContext, day: date) -> None:
    for inst in ctx.stocks:
        try:
            ctx.preps[inst.trading_symbol] = ctx.source.prep(inst, day)
        except Exception as exc:        # one bad symbol must not stop the worker
            ctx.preps[inst.trading_symbol] = None
            ctx.errors.append(f"{inst.trading_symbol}: prep failed: {exc}")


def run_tick(ctx: WorkerContext, as_of: datetime, generated_at: datetime) -> dict:
    errors = list(ctx.errors)
    index_bars = []
    if ctx.index is not None:
        try:
            index_bars = ctx.source.minute_candles(ctx.index, as_of)
        except Exception as exc:
            errors.append(f"{ctx.index.trading_symbol}: {exc}")
    market = nifty_context(index_bars, as_of, ctx.stale_after_seconds,
                           ctx.engine_cfg.market_ramp_pct)
    by_mode: dict = {m: [] for m in MODES}
    excluded, in_play, freshest = [], 0, None
    for inst in ctx.stocks:
        sym = inst.trading_symbol
        pr = ctx.preps.get(sym)
        try:
            inputs = SymbolInputs(sym, ctx.source.minute_candles(inst, as_of),
                                  pr.prep if pr else None, pr.volume_curve if pr else [],
                                  pr.liquidity if pr else None, index_bars)
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
    ctx = base_context(source, uni.stocks, index, "live", False)
    ctx.errors += [f"{s}: {r}" for s, r in uni.rejected.items()]
    return ctx


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Recommendation worker (never places orders).")
    p.add_argument("--replay", type=Path, help="replay dir of per-day 1-min CSVs")
    p.add_argument("--day", type=date.fromisoformat, help="replay day YYYY-MM-DD")
    p.add_argument("--speed", type=float, default=60.0,
                   help="simulated minutes per real minute; 0 = no delay")
    p.add_argument("--ticks", type=int, default=None, help="stop after N ticks")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = p.parse_args(argv)
    if a.replay:
        if a.day is None:
            p.error("--day is required with --replay")
        source = ReplaySource(a.replay, a.day)
        stocks, index = source.instruments()
        if not stocks:
            p.error(f"no candle files for {a.day} in {a.replay}")
        ctx = base_context(source, stocks, index, "replay", source.is_demo)
        day, clock = a.day, replay_clock(a.day)
        delay = 60.0 / a.speed if a.speed > 0 else 0.0
    else:
        ctx = _live_context()
        day, clock, delay = date.today(), live_clock(), 0.0
    prepare(ctx, day)
    for n, as_of in enumerate(clock, 1):
        write_state(a.out, run_tick(ctx, as_of, datetime.now()))
        if a.ticks and n >= a.ticks:
            break
        if delay:
            _time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
