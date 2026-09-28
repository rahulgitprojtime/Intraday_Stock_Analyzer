"""Build the market-wide intraday volume curve (M11, DECISIONS #21).

For every stock-day in a replay directory, the cumulative share of that
day's volume traded by the end of each of the 375 session minutes (missing
minutes carry the previous share). The median across stock-days is written
to config/market_volume_curve.json (committed; rerun to refresh):

    python scripts/build_volume_curve.py --replay data/replay_1y
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.models import SESSION_MINUTES  # noqa: E402
from src.quantitative.volume_scan import CURVE_FILE  # noqa: E402

INDEX_PREFIXES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "INDIAVIX", "MIDCAP")


def day_curve(path: Path) -> list[float] | None:
    per_min = [0.0] * SESSION_MINUTES
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ts = datetime.fromisoformat(r["timestamp"])
            i = (ts.hour * 60 + ts.minute) - (9 * 60 + 15)
            if 0 <= i < SESSION_MINUTES:
                per_min[i] += float(r["volume"])
    total = sum(per_min)
    if total <= 0:
        return None
    out, cum = [], 0.0
    for v in per_min:
        cum += v
        out.append(cum / total)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--replay", type=Path, required=True)
    a = p.parse_args(argv)
    curves = [c for f in sorted(a.replay.glob("*/*.csv"))
              if not f.stem.startswith(INDEX_PREFIXES) and (c := day_curve(f))]
    if not curves:
        p.error("no stock volume found")
    med = [median(c[i] for c in curves) for i in range(SESSION_MINUTES)]
    med = [max(med[: i + 1]) for i in range(SESSION_MINUTES)]      # keep it monotone
    med = [v / med[-1] for v in med]                                # end exactly at 1.0
    CURVE_FILE.write_text(json.dumps({
        "source": str(a.replay).replace("\\", "/"), "stock_days": len(curves),
        "method": "median cumulative share of daily volume by session minute",
        "cumulative_fraction": [round(v, 6) for v in med]}, indent=1), encoding="utf-8")
    print(f"{len(curves)} stock-days -> {CURVE_FILE}; share by 09:30 {med[14]:.3f}, "
          f"by 12:00 {med[164]:.3f}, by 15:00 {med[344]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
