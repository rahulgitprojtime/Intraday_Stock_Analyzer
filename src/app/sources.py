"""Candle sources for the worker — M6 (spec §22).

Both expose `minute_candles(inst, now)` (today's closed 1-min bars, never
later ones) and `prep(inst, day)`. Read-only market data; no orders.

- ReplaySource: per-day CSVs in the `IntradayCandleCache` layout. For
  engineering validation and deterministic development. Synthetic data is
  never evidence that the methodology is profitable.
- LiveSource: Groww 1-min REST history + incremental cache. Unrun until
  credentials and a live-price subscription exist.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from src.data.models import SESSION_OPEN, Candle, Exchange, HistoricalCandleRequest, Instrument, Segment
from src.data.prep_builder import PrepResult, build_prep
from src.storage.candle_cache import IntradayCandleCache

INDEX_SYMBOL = "NIFTY"
ONE_MIN = timedelta(minutes=1)


class ReplaySource:
    def __init__(self, root: str | Path, day: date) -> None:
        self.root = Path(root)
        self.day = day
        self._cache = IntradayCandleCache(self.root)
        self._loaded: dict = {}
        self._prep_cutoff = datetime.combine(day, SESSION_OPEN)   # prep never sees the replay day

    @property
    def is_demo(self) -> bool:
        return (self.root / "DEMO").exists()

    def instruments(self) -> tuple[list[Instrument], Instrument | None]:
        names = sorted(p.stem for p in (self.root / self.day.isoformat()).glob("*.csv"))
        stocks = [Instrument(n, Exchange.NSE, Segment.CASH) for n in names if n != INDEX_SYMBOL]
        index = (Instrument(INDEX_SYMBOL, Exchange.NSE, Segment.CASH, is_index=True)
                 if INDEX_SYMBOL in names else None)
        return stocks, index

    def _load(self, inst: Instrument, day: date) -> list[Candle]:
        key = (inst.trading_symbol, day)
        if key not in self._loaded:
            self._loaded[key] = self._cache.load(inst, day)
        return self._loaded[key]

    def minute_candles(self, inst: Instrument, now: datetime) -> list[Candle]:
        """Bars that had closed by `now` — never later ones."""
        return [c for c in self._load(inst, now.date()) if c.timestamp + ONE_MIN <= now]

    def get_historical_candles(self, request: HistoricalCandleRequest) -> list[Candle]:
        """Read-only history for `build_prep`, capped before the replay day."""
        end = min(request.end_time, self._prep_cutoff - ONE_MIN)
        out: list[Candle] = []
        d = request.start_time.date()
        while d <= end.date():
            out += [c for c in self._load(request.instrument, d)
                    if request.start_time <= c.timestamp <= end]
            d += timedelta(days=1)
        return out

    def prep(self, inst: Instrument, day: date) -> PrepResult | None:
        return build_prep(self, inst, day)


class LiveSource:
    def __init__(self, adapter, cache: IntradayCandleCache) -> None:
        self.adapter = adapter
        self.cache = cache

    def minute_candles(self, inst: Instrument, now: datetime) -> list[Candle]:
        return [c for c in self.cache.refresh(self.adapter, inst, now) if c.is_complete]

    def prep(self, inst: Instrument, day: date) -> PrepResult | None:
        return build_prep(self.adapter, inst, day)
