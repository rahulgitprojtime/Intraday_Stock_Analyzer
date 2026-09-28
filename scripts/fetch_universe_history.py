"""Download whole-market history for research — M16 (DECISIONS #26).

1. Daily candles for every NSE EQ intraday stock (≤ 180 days per call).
2. Per-day liquid pool from the 20 prior sessions (universe.yaml filters,
   price band) -> <out>/pools.json.
3. 1-min bars for every stock that was ever in a pool, plus NIFTY, BANK
   NIFTY and the sector indices, in the replay layout (<out>/<day>/<SYM>.csv).

Resumable (finished symbols are skipped) and paced to stay well under
Groww's limits. Read-only market data. Run outside market hours.

    python scripts/fetch_universe_history.py --first 2025-10-03 --last 2026-09-28
"""

from __future__ import annotations

import argparse
import json
import sys
import time as _time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fetch_replay_data import download_sessions  # noqa: E402
from src.broker.groww import MAX_DAILY_WINDOW_DAYS  # noqa: E402
from src.market.sector import load_sector_map  # noqa: E402
from src.quantitative.volume_scan import ScanFilters  # noqa: E402
from src.research.universe_history import build_pools, load_daily, save_daily, trading_days  # noqa: E402
from src.utils.config import load_settings, load_universe  # noqa: E402

STATS_LEAD_DAYS = 40            # calendar days before the first session, for 20 prior sessions


def paced(calls_per_minute: float):
    gap, last = 60.0 / calls_per_minute, [0.0]

    def wait(calls: int = 1) -> None:
        pause = last[0] + gap * calls - _time.monotonic()
        if pause > 0:
            _time.sleep(pause)
        last[0] = _time.monotonic()
    return wait


def fetch_daily(adapter, instruments, start: date, end: date, out: Path, wait) -> list[str]:
    errors = []
    for n, inst in enumerate(instruments, 1):
        sym = inst.trading_symbol
        if (out / f"{sym}.csv").exists():
            continue
        bars, s = [], start
        try:
            while s <= end:
                e = min(end, s + timedelta(days=MAX_DAILY_WINDOW_DAYS))
                wait()
                bars += adapter.get_daily_candles(inst, s, e)
                s = e + timedelta(days=1)
            save_daily(out, sym, bars)
        except Exception as exc:              # one bad symbol never stops the run; not cached
            errors.append(f"daily {sym}: {type(exc).__name__}: {exc}")
        if n % 100 == 0:
            print(f"  daily {n}/{len(instruments)} ({len(errors)} errors)", flush=True)
    return errors


def main(argv=None) -> int:
    from dotenv import load_dotenv

    from src.broker.groww import GrowwAdapter
    from src.broker.groww_instruments import InstrumentMaster

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--first", type=date.fromisoformat, required=True, help="first 1-min session")
    p.add_argument("--last", type=date.fromisoformat, required=True, help="last 1-min session")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "universe_1y")
    p.add_argument("--calls-per-minute", type=float, default=150)
    a = p.parse_args(argv)

    load_dotenv(ROOT / ".env")
    storage = load_settings()["storage"]
    master = InstrumentMaster.load(Path(storage["data_dir"]) / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    adapter.authenticate()
    wait = paced(a.calls_per_minute)
    stocks = master.scan_universe()

    print(f"1/3 daily candles for {len(stocks)} stocks ...", flush=True)
    errors = fetch_daily(adapter, stocks, a.first - timedelta(days=STATS_LEAD_DAYS), a.last,
                         a.out / "daily", wait)
    for e in errors[:10]:
        print("  " + e)

    print("2/3 liquid pools per day ...", flush=True)
    daily = {i.trading_symbol: load_daily(a.out / "daily", i.trading_symbol) for i in stocks}
    daily = {s: b for s, b in daily.items() if b}
    days = [d for d in trading_days(daily) if a.first <= d <= a.last]
    pools = build_pools(daily, days, ScanFilters.from_config(load_universe().get("filters", {})))
    (a.out / "pools.json").write_text(json.dumps(pools), encoding="utf-8")
    ever = sorted({s for pool in pools.values() for s in pool})
    sizes = [len(v) for v in pools.values()]
    print(f"  {len(days)} days, pool size {min(sizes)}..{max(sizes)}, {len(ever)} stocks ever in a pool",
          flush=True)

    sectors, _ = load_sector_map(load_universe().get("symbols") or [])
    index_names = sorted({"NIFTY", "BANKNIFTY"} | {s["index"] for s in sectors.values()})
    todo = [master.resolve(s) for s in ever]
    for name in index_names:
        try:
            todo.append(adapter.resolve_instrument(name, "NSE", "CASH"))
        except ValueError as exc:
            print(f"  SKIP {name}: {exc}")
    done_dir = a.out / "_done"
    done_dir.mkdir(parents=True, exist_ok=True)
    print(f"3/3 1-min bars for {len(todo)} instruments x {len(days)} sessions ...", flush=True)
    calls_each = (a.last - a.first).days // 30 + 1
    fails = 0
    for n, inst in enumerate(todo, 1):
        mark = done_dir / inst.trading_symbol
        if mark.exists():
            continue
        wait(calls_each)
        rep = download_sessions(adapter, [inst], a.last, len(days), a.out)
        got = rep[inst.trading_symbol]
        if isinstance(got, int):
            mark.write_text(str(got), encoding="utf-8")
        else:
            fails += 1
            print(f"  FAIL {inst.trading_symbol}: {got}", flush=True)
        if n % 25 == 0:
            print(f"  1-min {n}/{len(todo)} ({fails} failed)", flush=True)
    print(f"done: {len(todo) - fails}/{len(todo)} instruments -> {a.out}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
