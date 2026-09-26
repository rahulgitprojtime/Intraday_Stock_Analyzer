"""Tiny replay directory for worker/source tests (synthetic, engineering only)."""

from datetime import date, datetime, time, timedelta
from pathlib import Path

from src.data.models import Candle, Exchange, Instrument, Segment
from src.storage.candle_cache import IntradayCandleCache

REPLAY_DAY = date(2026, 9, 25)


def write_replay_fixture(root: Path, day: date = REPLAY_DAY, symbols=("AAA", "BBB"),
                         prior_days: int = 2, bars_per_day: int = 30, with_index: bool = True):
    cache = IntradayCandleCache(root)
    days = [day - timedelta(days=k) for k in range(prior_days, 0, -1)] + [day]
    names = list(symbols) + (["NIFTY"] if with_index else [])
    for n, sym in enumerate(names):
        inst = Instrument(sym, Exchange.NSE, Segment.CASH, is_index=sym == "NIFTY")
        base = 25000.0 if sym == "NIFTY" else 500.0 + 100 * n
        vol = 0 if sym == "NIFTY" else 20000
        for d in days:
            start = datetime.combine(d, time(9, 15))
            bars = []
            for i in range(bars_per_day):
                o = base + i * 0.1 * (1 + n)
                c = o + 0.1
                bars.append(Candle(inst, 1, start + timedelta(minutes=i), o, c + 0.05, o - 0.05,
                                   c, vol))
            cache.save(inst, d, bars)
    return names
