"""Intraday 1-min candle cache — M3.

One CSV per symbol per day under `<root>/<YYYY-MM-DD>/<SYMBOL>.csv`
(stdlib csv, not parquet: DECISIONS.md #12). `refresh` fetches only from
the last cached bar onward — that bar is re-fetched because it may have
been forming — so each stock costs one REST call per minute.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from src.data.models import SESSION_OPEN, Candle, HistoricalCandleRequest, Instrument

_FIELDS = ["timestamp", "open", "high", "low", "close", "volume", "is_complete"]


class IntradayCandleCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, instrument: Instrument, day: date) -> Path:
        return self.root / day.isoformat() / f"{instrument.trading_symbol}.csv"

    def load(self, instrument: Instrument, day: date) -> list[Candle]:
        path = self._path(instrument, day)
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as f:
            return [
                Candle(
                    instrument=instrument,
                    timeframe_minutes=1,
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=int(r["volume"]),
                    is_complete=r["is_complete"] == "1",
                )
                for r in csv.DictReader(f)
            ]

    def save(self, instrument: Instrument, day: date, candles: list[Candle]) -> None:
        path = self._path(instrument, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(_FIELDS)
            for c in candles:
                w.writerow([c.timestamp.isoformat(), c.open, c.high, c.low, c.close,
                            c.volume, int(c.is_complete)])
        tmp.replace(path)  # atomic: the dashboard never reads a half-written file

    def refresh(self, adapter, instrument: Instrument, now: datetime) -> list[Candle]:
        day = now.date()
        cached = self.load(instrument, day)
        start = cached[-1].timestamp if cached else datetime.combine(day, SESSION_OPEN)
        fresh = adapter.get_historical_candles(
            HistoricalCandleRequest(instrument, start, now, interval_minutes=1)
        )
        merged = {c.timestamp: c for c in cached}
        merged.update({c.timestamp: c for c in fresh})
        candles = []
        for ts in sorted(merged):
            c = merged[ts]
            complete = now >= ts + timedelta(minutes=1)
            if c.is_complete != complete:
                c = Candle(c.instrument, 1, c.timestamp, c.open, c.high, c.low, c.close,
                           c.volume, complete)
            candles.append(c)
        self.save(instrument, day, candles)
        return candles
