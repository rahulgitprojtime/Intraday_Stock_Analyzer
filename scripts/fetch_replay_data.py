"""Download real 1-min sessions from Groww into replay format.

Writes the last N sessions ending at `--day` for the configured universe
+ NIFTY + the sector indices in `config/sectors.yaml` (M8) into
`IntradayCandleCache` layout (`<out>/<day>/<SYMBOL>.csv`), readable by `ReplaySource`. Read-only market data; no orders
(DECISIONS #8). Real data is still not strategy evidence without
walk-forward validation (M10).

    python scripts/fetch_replay_data.py --day 2026-09-25 --out data/replay
    python -m src.app.worker --replay data/replay --day 2026-09-25 --speed 0
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.models import SESSION_OPEN, HistoricalCandleRequest, Instrument  # noqa: E402
from src.storage.candle_cache import IntradayCandleCache  # noqa: E402

SESSION_CLOSE = time(15, 30)


def download_sessions(adapter, instruments: list[Instrument], day: date, sessions: int,
                      out: str | Path) -> dict[str, int | str]:
    """Save the last `sessions` trading days ending at `day` per instrument.

    Returns symbol -> sessions written, or "error: ..." (one bad symbol
    must not stop the download).
    """
    cache = IntradayCandleCache(out)
    start = datetime.combine(day - timedelta(days=sessions * 7 // 5 + 10), SESSION_OPEN)
    end = datetime.combine(day, SESSION_CLOSE)
    report: dict[str, int | str] = {}
    for inst in instruments:
        try:
            candles = adapter.get_historical_candles(
                HistoricalCandleRequest(inst, start, end, interval_minutes=1))
        except Exception as exc:
            report[inst.trading_symbol] = f"error: {type(exc).__name__}: {exc}"
            continue
        by_day: dict[date, list] = defaultdict(list)
        for c in candles:
            by_day[c.timestamp.date()].append(c)
        kept = sorted(by_day)[-sessions:]
        for d in kept:
            cache.save(inst, d, by_day[d])
        report[inst.trading_symbol] = len(kept)
    return report


def main(argv=None) -> int:
    from dotenv import load_dotenv

    from src.broker.groww import GrowwAdapter
    from src.broker.groww_instruments import InstrumentMaster
    from src.data.universe import resolve_universe
    from src.market.sector import load_sector_map
    from src.utils.config import load_settings, load_universe

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--day", type=date.fromisoformat, required=True, help="last session YYYY-MM-DD")
    p.add_argument("--sessions", type=int, default=21, help="replay day + 20 prep sessions")
    p.add_argument("--out", type=Path, default=Path("data/replay"))
    p.add_argument("--symbols-file", type=Path, default=None,
                   help="stocks to fetch, one per line (e.g. scan_now --save); default universe.yaml")
    a = p.parse_args(argv)

    load_dotenv(ROOT / ".env")          # credentials; never printed
    storage = load_settings()["storage"]
    data_dir = ROOT / storage["data_dir"]
    master = InstrumentMaster.load(data_dir / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    adapter.authenticate()
    cfg = load_universe()
    if a.symbols_file:
        cfg = cfg | {"symbols": [x.strip() for x in a.symbols_file.read_text(encoding="utf-8")
                                 .splitlines() if x.strip()],
                     "filters": cfg.get("filters", {}) | {"max_universe_size": 10_000}}
    uni = resolve_universe(cfg, lambda s, e: adapter.resolve_instrument(s, e, "CASH"))
    for sym, reason in uni.rejected.items():
        print(f"SKIP  {sym}: {reason}")
    index = [i for i in uni.indices if i.trading_symbol == "NIFTY"]
    sectors, problems = load_sector_map(load_universe().get("symbols") or [])
    for problem in problems:
        print(f"SKIP  sectors.yaml: {problem}")
    sector_idx = []
    for name in sorted({s["index"] for s in sectors.values()} | {"BANKNIFTY"}):
        try:
            sector_idx.append(adapter.resolve_instrument(name, "NSE", "CASH"))
        except ValueError as exc:
            print(f"SKIP  {name}: {exc}")
    report = download_sessions(adapter, uni.stocks + index + sector_idx, a.day, a.sessions,
                               a.out)
    for sym, n in report.items():
        print(f"{'OK  ' if isinstance(n, int) else 'FAIL'}  {sym}: {n}")
    ok = sum(isinstance(n, int) and n == a.sessions for n in report.values())
    print(f"\n{ok}/{len(report)} symbols with {a.sessions} sessions -> {a.out}")
    return 0 if ok == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
