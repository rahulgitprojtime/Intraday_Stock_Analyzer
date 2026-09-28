"""Label a day's research snapshots with forward outcomes — M14 (DECISIONS #24).

After the close: download that day's 1-min bars for every snapshot symbol
(plus NIFTY, sector indices, BANK NIFTY), then write labeled rows.

    python scripts/label_outcomes.py --day 2026-09-29 --fetch
    python scripts/label_outcomes.py --day 2026-09-25 --snapshots S --bars data/replay
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.models import Exchange, Instrument, Segment  # noqa: E402
from src.research.outcomes import label_file  # noqa: E402
from src.storage.candle_cache import IntradayCandleCache  # noqa: E402

LIVE = ROOT / "data" / "research" / "live"


def bar_loader(root: Path):
    cache = IntradayCandleCache(root)
    return lambda sym, day: cache.load(Instrument(sym, Exchange.NSE, Segment.CASH),
                                       date.fromisoformat(day))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--day", type=date.fromisoformat, required=True)
    p.add_argument("--snapshots", type=Path, default=LIVE / "snapshots")
    p.add_argument("--bars", type=Path, default=LIVE / "bars")
    p.add_argument("--out", type=Path, default=LIVE / "labeled")
    p.add_argument("--fetch", action="store_true", help="download the day's bars from Groww first")
    a = p.parse_args(argv)
    snaps = a.snapshots / f"{a.day.isoformat()}.jsonl"
    if not snaps.exists():
        print(f"no snapshots for {a.day}: {snaps}")
        return 1
    if a.fetch:
        from scripts.fetch_replay_data import main as fetch
        symbols = sorted({json.loads(x)["symbol"] for x in snaps.read_text(encoding="utf-8").splitlines() if x})
        listing = a.bars / f"symbols_{a.day.isoformat()}.txt"
        listing.parent.mkdir(parents=True, exist_ok=True)
        listing.write_text("".join(s + "\n" for s in symbols), encoding="utf-8")
        print(f"fetching 1-min bars for {len(symbols)} symbols + indices ...", flush=True)
        fetch(["--day", a.day.isoformat(), "--sessions", "1", "--out", str(a.bars),
               "--symbols-file", str(listing)])
    n, labeled = label_file(snaps, bar_loader(a.bars), a.out / f"{a.day.isoformat()}.jsonl")
    print(f"labeled {n} rows ({labeled} with a 5-min outcome) -> {a.out}")
    return 0 if labeled else 1


if __name__ == "__main__":
    raise SystemExit(main())
