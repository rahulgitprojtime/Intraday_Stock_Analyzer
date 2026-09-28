"""Whole-market history for research — M16 (DECISIONS #26).

The live worker picks its universe each minute from every liquid NSE
intraday stock (M11-M13). To research the same candidates historically:

- `build_pools`: for each past day, the liquid pool from daily candles of
  the 20 sessions BEFORE that day (no look-ahead), same filters as live
  (price band, average volume, average traded value).
- `ReplayScanner`: the live scanner's interface (`ranked`, `record`,
  `status`) over saved 1-min bars. At minute T each pool stock's "quote"
  is built from bars that closed by T only; ranking and the sticky top N
  reuse the live code (`rank_volume_change`, `ActiveSet`, `DynamicUniverse`).

Difference from live, stated in reports: history knows every pool stock's
volume each minute; live quotes volume only for the top movers (M13).
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from src.data.models import Candle, Exchange, Instrument, Segment
from src.quantitative.volume_scan import DailyStats, ScanFilters, daily_stats, rank_volume_change

ONE_MIN = timedelta(minutes=1)
_DAILY_FIELDS = ("date", "open", "high", "low", "close", "volume")


# -- daily candles on disk ---------------------------------------------------------

def save_daily(root: str | Path, symbol: str, bars: list[Candle]) -> None:
    path = Path(root) / f"{symbol}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_DAILY_FIELDS)
        for b in bars:
            w.writerow((b.timestamp.date().isoformat(), "" if b.open is None else b.open,
                        b.high, b.low, b.close, b.volume))
    tmp.replace(path)


def load_daily(root: str | Path, symbol: str) -> list[Candle]:
    path = Path(root) / f"{symbol}.csv"
    if not path.exists():
        return []
    inst = Instrument(symbol, Exchange.NSE, Segment.CASH)
    with path.open(newline="", encoding="utf-8") as f:
        return [Candle(inst, 1440, datetime.combine(date.fromisoformat(r["date"]), time()),
                       float(r["open"]) if r["open"] else None, float(r["high"]),
                       float(r["low"]), float(r["close"]), int(float(r["volume"])))
                for r in csv.DictReader(f)]


# -- pools ---------------------------------------------------------------------------

def trading_days(daily: dict[str, list[Candle]], min_share: float = 0.5) -> list[date]:
    """Dates on which at least `min_share` of the symbols have a daily bar."""
    seen = Counter(b.timestamp.date() for bars in daily.values() for b in bars)
    need = min_share * len(daily)
    return sorted(d for d, n in seen.items() if n >= need)


def build_pools(daily: dict[str, list[Candle]], days: list[date], filters: ScanFilters,
                sessions: int = 20) -> dict[str, dict[str, dict]]:
    """day -> {symbol: DailyStats as dict} for the stocks in that day's liquid pool."""
    out: dict[str, dict[str, dict]] = {}
    for day in days:
        pool = {}
        for sym, bars in sorted(daily.items()):
            st = daily_stats(sym, bars, day, sessions)
            if (st is not None and filters.price_ok(st.prev_close)
                    and st.avg_volume >= filters.min_avg_daily_volume
                    and st.avg_traded_value >= filters.min_avg_traded_value):
                pool[sym] = asdict(st)
        out[day.isoformat()] = pool
    return out


# -- replay scanner --------------------------------------------------------------------

class _Series:
    """Running day values per closed minute: cum volume, high, low, VWAP."""

    def __init__(self, bars: list[Candle]) -> None:
        bars = sorted((b for b in bars if b.is_complete), key=lambda b: b.timestamp)
        self.ends = [b.timestamp + ONE_MIN for b in bars]      # a bar is usable once it closed
        self.close = [b.close for b in bars]
        self.open0 = bars[0].open if bars else None
        self.cumvol, self.high, self.low, self.vwap = [], [], [], []
        v = tpv = 0.0
        hi, lo = float("-inf"), float("inf")
        for b in bars:
            v += b.volume
            tpv += (b.high + b.low + b.close) / 3 * b.volume
            hi, lo = max(hi, b.high), min(lo, b.low)
            self.cumvol.append(int(v))
            self.high.append(hi)
            self.low.append(lo)
            self.vwap.append(tpv / v if v else None)

    def closed_by(self, now: datetime) -> int:
        return bisect_right(self.ends, now)


class ReplayScanner:
    def __init__(self, day: date, stats: dict[str, DailyStats], load_bars: Callable[[str], list],
                 filters: ScanFilters, curve: list[float]) -> None:
        self.day, self.stats, self.filters, self.curve = day, stats, filters, curve
        self.pool = sorted(stats)
        self._load, self._series = load_bars, {}

    def _get(self, sym: str) -> _Series:
        if sym not in self._series:
            self._series[sym] = _Series(self._load(sym))
        return self._series[sym]

    def ranked(self, now: datetime):
        quotes = []
        for sym in self.pool:
            s = self._get(sym)
            k = s.closed_by(now)
            if k == 0:
                continue
            j = s.closed_by(now - ONE_MIN)
            quotes.append({"symbol": sym, "volume": s.cumvol[k - 1], "last_price": s.close[k - 1],
                           "open": s.open0, "high": s.high[k - 1], "low": s.low[k - 1],
                           "average_price": s.vwap[k - 1], "at": now,
                           "prev_volume": s.cumvol[j - 1] if j else None,
                           "prev_at": now - ONE_MIN if j else None})
        return rank_volume_change(quotes, self.stats, self.filters, now.time(), self.curve)

    def record(self, now: datetime, active: list[str]) -> None:
        pass                                   # history: the research snapshots are the record

    def status(self) -> dict:
        return {"pool": len(self.pool), "quoted": len(self.pool), "full_sweeps": None}
