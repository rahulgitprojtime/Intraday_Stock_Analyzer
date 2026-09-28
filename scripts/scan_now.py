"""Run the market-wide volume scan once and print the top N (M11). Read-only.

Uses the same scanner as the live worker: daily stats for every NSE
EQ-series intraday stock (cached per day), then one full quote sweep of the
liquid pool, then the top N by volume change (long-only).

    python scripts/scan_now.py --top 25
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.app.market_scanner import MarketScanner, ScannerConfig  # noqa: E402
from src.broker.groww import GrowwAdapter  # noqa: E402
from src.broker.groww_instruments import InstrumentMaster  # noqa: E402
from src.quantitative.volume_scan import ScanFilters, load_market_curve  # noqa: E402
from src.utils.config import load_settings, load_universe  # noqa: E402


def show_errors(errors: list[str], limit: int = 5) -> None:
    """Distinct error messages (symbol stripped), most common first."""
    counts: dict[str, int] = {}
    for e in errors:
        msg = e.split(": ", 1)[-1]
        counts[msg] = counts.get(msg, 0) + 1
    for msg, n in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]:
        print(f"    {n:>5} x {msg[:160]}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--top", type=int, default=25)
    a = p.parse_args(argv)
    load_dotenv(ROOT / ".env")
    storage = load_settings()["storage"]
    data_dir = ROOT / storage["data_dir"]
    master = InstrumentMaster.load(data_dir / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    adapter.authenticate()
    uni = load_universe()
    f, scan = uni.get("filters", {}), uni.get("scan") or {}
    scanner = MarketScanner(
        adapter, master.scan_universe(),
        ScanFilters(float(f["min_price"]), float(f["min_avg_daily_volume"]),
                    float(f["min_avg_traded_value"]), bool(scan.get("long_only", True))),
        ScannerConfig.from_dict(scan | {"top_n": a.top}), load_market_curve(),
        data_dir / "cache", data_dir / "scans")
    today = date.today()
    errs = scanner.prepare(today, progress=lambda n, t: print(f"  daily stats {n}/{t}",
                                                              flush=True))
    print(f"stats {len(scanner.stats)}/{len(scanner.instruments)}, liquid pool {len(scanner.pool)},"
          f" prep errors {len(errs)}", flush=True)
    show_errors(errs)
    now = datetime.now()
    errs = scanner.sweep_step(len(scanner.pool), now)
    print(f"quoted {scanner.status()['quoted']}/{len(scanner.pool)}, quote errors {len(errs)}")
    show_errors(errs)
    ranked = scanner.ranked(now)
    print(f"\n{len(ranked)} stocks up on the day and liquid; top {a.top} by volume change "
          f"at {now:%H:%M}:")
    for i, c in enumerate(ranked[: a.top], 1):
        print(f"{i:>3} {c.symbol:<14} vol x{c.volume_change:6.2f}  day {c.day_change_pct:+6.2f}%"
              f"  volume {c.volume:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
