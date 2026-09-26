"""Synthetic DEMO data for replay (spec §23).

Engineering only: seeded random walks with a few engineered patterns on
the replay day (DEMO01 gap-up with heavy volume, DEMO02 opening-range
breakout, DEMO03 VWAP reclaim, DEMO10 illiquid). Engineered demo patterns
are not strategy evidence; never tune setups on this data. A `DEMO`
marker file makes the worker flag the state as demo.

    python scripts/make_demo_data.py --out data/demo --day 2026-09-25
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.models import SESSION_MINUTES, Candle, Exchange, Instrument, Segment  # noqa: E402
from src.storage.candle_cache import IntradayCandleCache  # noqa: E402

STOCKS = [f"DEMO{i:02d}" for i in range(1, 11)]
INDEX = "NIFTY"


def _sessions(day: date, n: int) -> list[date]:
    out, d = [], day
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _profile(i: int) -> float:
    """U-shaped intraday volume."""
    x = i / (SESSION_MINUTES - 1)
    return 0.6 + 1.6 * (1 - x) ** 6 + 0.8 * x ** 6


def _day(inst, d, open_px, base_vol, rng, *, drift=0.0, vol_mult=1.0, pattern=None):
    bars, px = [], open_px
    start = datetime.combine(d, time(9, 15))
    for i in range(SESSION_MINUTES):
        step, m = rng.gauss(drift, 0.0008), vol_mult
        if pattern == "orb":
            step = rng.gauss(0, 0.0003) if i < 20 else rng.gauss(0.0006, 0.0006)
            m = vol_mult if i < 20 else vol_mult * 3
        elif pattern == "reclaim":
            if 15 <= i < 45:
                step = rng.gauss(-0.0006, 0.0005)
            elif i >= 45:
                step, m = rng.gauss(0.0007, 0.0005), vol_mult * 2.5
        o = round(px, 2)
        c = round(px * (1 + step), 2)
        h = round(max(o, c) * (1 + abs(rng.gauss(0, 0.0003))), 2)
        low = round(min(o, c) * (1 - abs(rng.gauss(0, 0.0003))), 2)
        v = int(base_vol * _profile(i) * m * rng.lognormvariate(0, 0.3)) if base_vol else 0
        bars.append(Candle(inst, 1, start + timedelta(minutes=i), o, h, low, c, v))
        px = c
    return bars


def generate_demo(root: str | Path, day: date, sessions: int = 21, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    cache = IntradayCandleCache(root)
    days = _sessions(day, sessions)
    replay_day = {"DEMO01": dict(drift=0.0004, vol_mult=3.0), "DEMO02": dict(pattern="orb"),
                  "DEMO03": dict(pattern="reclaim"), INDEX: dict(drift=0.00005)}
    for sym in STOCKS + [INDEX]:
        inst = Instrument(sym, Exchange.NSE, Segment.CASH, is_index=sym == INDEX)
        px = 25000.0 if sym == INDEX else rng.uniform(200, 1500)
        base_vol = 0 if sym == INDEX else (40 if sym == "DEMO10" else rng.uniform(3000, 8000))
        for d in days:
            gap = 1.022 if (d == day and sym == "DEMO01") else 1 + rng.gauss(0, 0.004)
            kw = replay_day.get(sym, {}) if d == day else {}
            bars = _day(inst, d, round(px * gap, 2), base_vol, rng, **kw)
            cache.save(inst, d, bars)
            px = bars[-1].close
    Path(root, "DEMO").write_text("Synthetic demo data - not market data, not strategy "
                                  "evidence.\n", encoding="utf-8")
    return STOCKS + [INDEX]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=Path("data/demo"))
    p.add_argument("--day", type=date.fromisoformat, required=True, help="replay day YYYY-MM-DD")
    p.add_argument("--sessions", type=int, default=21)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)
    names = generate_demo(a.out, a.day, a.sessions, a.seed)
    print(f"Wrote {len(names)} symbols x {a.sessions} sessions to {a.out} (DEMO)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
