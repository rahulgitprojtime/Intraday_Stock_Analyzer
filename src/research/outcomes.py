"""Forward outcomes for candidate snapshots — M14 (DECISIONS #24).

A snapshot at minute T was scored on bars that closed by T, so the entry
is the open of the bar that starts at T. For each horizon h the exit is
the close of the bar starting at T+h-1. Also: best/worst move inside the
window (MFE/MAE, from highs/lows), return in excess of NIFTY and of the
stock's sector index over the same window, and the return after an
assumed round-trip cost. Windows ending after 15:25 are flagged
`truncated` and left empty; missing bars give None — never a guess.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path

HORIZONS = (5, 15, 30, 60)
COST_PCT = 0.1                   # assumed round trip incl. brokerage, STT, slippage
LAST_EXIT = time(15, 25)
ONE_MIN = timedelta(minutes=1)


def _window(bars_by_ts: dict, t0: datetime, h: int):
    """(entry, close at the end, max high, min low) or None if any bar is missing."""
    win = [bars_by_ts.get(t0 + i * ONE_MIN) for i in range(h)]
    if any(b is None for b in win):
        return None
    return win[0].open, win[-1].close, max(b.high for b in win), min(b.low for b in win)


def _pct(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0


def label_row(row: dict, load_bars: Callable[[str], list], horizons=HORIZONS,
              cost_pct: float = COST_PCT) -> dict:
    t0 = datetime.fromisoformat(row["as_of"])
    series = {}
    for name in (row["symbol"], "NIFTY", row.get("sector_index")):
        if name and name not in series:
            series[name] = {b.timestamp: b for b in load_bars(name)}
    out = dict(row)
    for h in horizons:
        truncated = (t0 + h * ONE_MIN).time() > LAST_EXIT
        out[f"truncated_{h}"] = truncated
        w = None if truncated else _window(series[row["symbol"]], t0, h)
        keys = ("fwd", "net", "mfe", "mae", "xs_nifty", "xs_sector")
        out |= {f"{k}_{h}": None for k in keys}
        if w is None:
            continue
        entry, close, hi, lo = w
        ret = _pct(entry, close)
        out |= {f"fwd_{h}": ret, f"net_{h}": ret - cost_pct,
                f"mfe_{h}": _pct(entry, hi), f"mae_{h}": _pct(entry, lo)}
        for key, name in (("xs_nifty", "NIFTY"), ("xs_sector", row.get("sector_index"))):
            iw = _window(series[name], t0, h) if name else None
            if iw is not None:
                out[f"{key}_{h}"] = ret - _pct(iw[0], iw[1])
    return out


def label_file(snapshots: Path, load_bars: Callable[[str, str], list], out_path: Path,
               horizons=HORIZONS, cost_pct: float = COST_PCT) -> tuple[int, int]:
    """Label every row of one day's snapshot file. Returns (rows, rows with a 5-min label)."""
    cache: dict = {}

    def bars(sym, day):
        if (sym, day) not in cache:
            cache[(sym, day)] = load_bars(sym, day)
        return cache[(sym, day)]

    rows = [json.loads(x) for x in snapshots.read_text(encoding="utf-8").splitlines() if x]
    labeled = [label_row(r, lambda s, d=r["day"]: bars(s, d), horizons, cost_pct) for r in rows]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(r) + "\n" for r in labeled), encoding="utf-8")
    first = f"fwd_{horizons[0]}"
    return len(labeled), sum(r[first] is not None for r in labeled)
