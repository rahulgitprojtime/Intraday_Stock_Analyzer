"""Backtest a strategy on cached 1-min candles (DECISIONS #29). SIMULATION ONLY.

    python scripts/backtest.py --strategy orb --cache data/replay_1y --from 2026-06-01 --to 2026-09-25
    python scripts/backtest.py --strategy orb --symbols RELIANCE INFY --from 2026-09-01 --to 2026-09-25 --fetch

Candles are read from `<cache>/<day>/<SYMBOL>.csv` (the layout
fetch_replay_data.py writes). `--fetch` downloads missing PAST days once
through the Groww adapter (read-only historical candles, needs .env) and
caches them. Bars are released at their close; orders fill from the next
bar (no look-ahead); costs from config/costs.yaml; square-off 15:15.
Results go to the SQLite ledger (paper.yaml backtest_ledger), shown on the
dashboard's Paper trading page. Measurements, not probabilities of profit.
The recommendation strategy is replayed by scripts/paper_replay.py.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paper.backtest import ensure_cached, load_day, run_backtest, weekdays  # noqa: E402
from src.paper.broker import BrokerConfig  # noqa: E402
from src.paper.costs import CostModel  # noqa: E402
from src.paper.ledger import Ledger  # noqa: E402
from src.paper.strategies.orb import OpeningRangeBreakout  # noqa: E402
from src.storage.candle_cache import IntradayCandleCache  # noqa: E402
from src.utils.config import load_universe, load_yaml  # noqa: E402

STRATEGIES = {"orb": lambda raw, symbols: OpeningRangeBreakout.from_dict(
    raw["orb"] | ({"symbols": symbols} if symbols else {}))}


def index_names() -> set[str]:
    """Index CSVs share the replay folders; they are context, not tradable."""
    sectors = load_yaml("sectors.yaml").get("sectors") or {}
    return ({s.upper() for s in load_universe().get("indices") or []} | {"BANKNIFTY"}
            | {str(v.get("index", "")).upper() for v in sectors.values() if isinstance(v, dict)})


def day_symbols(cache: IntradayCandleCache, day: date, symbols: list[str] | None,
                skip: set[str]) -> list[str]:
    if symbols:
        return symbols
    folder = cache.root / day.isoformat()
    return sorted(p.stem for p in folder.glob("*.csv") if p.stem.upper() not in skip)


def fetch_missing(cache: IntradayCandleCache, symbols: list[str], days: list[date]) -> None:
    from dotenv import load_dotenv

    from src.broker.groww import GrowwAdapter
    from src.broker.groww_instruments import InstrumentMaster
    from src.utils.config import load_settings

    load_dotenv(ROOT / ".env")                       # credentials; never printed
    storage = load_settings()["storage"]
    data_dir = ROOT / storage["data_dir"]
    adapter = GrowwAdapter(InstrumentMaster.load(data_dir / storage["instrument_cache_file"],
                                                 storage["instrument_cache_max_age_hours"]))
    adapter.authenticate()
    for sym in symbols:
        try:
            n = ensure_cached(cache, adapter, adapter.resolve_instrument(sym, "NSE", "CASH"),
                              days, date.today())
            print(f"cache {sym}: {n} day(s) downloaded")
        except Exception as exc:                      # one bad symbol must not stop the run
            print(f"cache {sym}: FAILED {type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="orb")
    p.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    p.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    p.add_argument("--cache", type=Path, default=Path("data/replay_1y"))
    p.add_argument("--symbols", nargs="*", default=None, help="default: every stock in the cache")
    p.add_argument("--fetch", action="store_true", help="download missing days (needs --symbols)")
    p.add_argument("--ledger", type=Path, default=None)
    p.add_argument("--run-id", default=None)
    a = p.parse_args(argv)
    if a.fetch and not a.symbols:
        p.error("--fetch needs --symbols")
    raw = load_yaml("paper.yaml")
    cfg, costs = BrokerConfig.from_dict(raw["broker"]), CostModel.from_dict(load_yaml("costs.yaml"))
    strategy = STRATEGIES[a.strategy](raw, a.symbols)
    cache = IntradayCandleCache(a.cache)
    days = weekdays(a.start, a.end)
    if a.fetch:
        fetch_missing(cache, a.symbols, days)
    skip = index_names()
    run_id = a.run_id or f"{strategy.name}:{a.start}..{a.end}"
    ledger = Ledger(a.ledger or raw["backtest_ledger"])
    res = run_backtest(strategy, ((d, load_day(cache, day_symbols(cache, d, a.symbols, skip), d))
                                  for d in days), cfg, costs, ledger=ledger, run_id=run_id)
    ledger.close()
    print(f"\nBACKTEST {run_id}  (SIMULATION ONLY - no orders were placed)")
    for k, v in res.metrics.items():
        print(f"  {k:18} {v:,.2f}" if isinstance(v, float) else f"  {k:18} {v}")
    print(f"ledger: {ledger.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
