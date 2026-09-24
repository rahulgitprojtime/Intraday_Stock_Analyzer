"""Groww instrument master — M2.

Loads Groww's public instruments CSV (verified 2026-09-25, see
docs/groww_api_notes.md) and resolves CASH-segment symbols to
`Instrument`s carrying the `exchange_token` the live feed needs.

The CSV is ~20 MB and mostly F&O rows; only CASH-segment EQ/IDX rows are
kept in memory. The file is cached on disk and re-downloaded once it is
older than `max_age_hours` (the master changes daily with listings,
series changes, etc.). Groww-specific: nothing outside `src/broker/`
should parse this file.
"""

from __future__ import annotations

import csv
import io
import time
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from src.data.models import Exchange, Instrument, Segment

INSTRUMENTS_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"

_KEPT_TYPES = {"EQ", "IDX"}


def _download(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https URL
        return resp.read().decode("utf-8")


class InstrumentMaster:
    """In-memory index of CASH-segment instruments keyed by (exchange, symbol)."""

    def __init__(self, rows: Iterable[dict]) -> None:
        self._by_symbol: dict[tuple[str, str], dict] = {}
        for row in rows:
            if row.get("segment") != Segment.CASH.value:
                continue
            if row.get("instrument_type") not in _KEPT_TYPES:
                continue
            if row.get("exchange") not in Exchange.__members__:
                continue
            self._by_symbol[(row["exchange"], row["trading_symbol"].upper())] = row

    def __len__(self) -> int:
        return len(self._by_symbol)

    @classmethod
    def from_csv_text(cls, text: str) -> InstrumentMaster:
        return cls(csv.DictReader(io.StringIO(text)))

    @classmethod
    def load(
        cls,
        cache_path: Path,
        max_age_hours: float = 20.0,
        fetch: Callable[[str], str] = _download,
    ) -> InstrumentMaster:
        """Load from the disk cache, downloading first if missing or stale.
        If a refresh download fails but a (stale) cache exists, the stale
        cache is used rather than failing the whole app — instrument tokens
        rarely change intraday."""
        cache_path = Path(cache_path)
        fresh = cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < max_age_hours * 3600
        if not fresh:
            try:
                text = fetch(INSTRUMENTS_CSV_URL)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding="utf-8")
            except Exception:
                if not cache_path.exists():
                    raise
        return cls.from_csv_text(cache_path.read_text(encoding="utf-8"))

    def get_row(self, trading_symbol: str, exchange: str) -> dict | None:
        return self._by_symbol.get((exchange, trading_symbol.upper()))

    def resolve(self, trading_symbol: str, exchange: str = "NSE") -> Instrument:
        row = self.get_row(trading_symbol, exchange)
        if row is None:
            raise KeyError(f"{exchange}:{trading_symbol} not found in CASH instrument master")
        return Instrument(
            trading_symbol=row["trading_symbol"],
            exchange=Exchange(row["exchange"]),
            segment=Segment.CASH,
            exchange_token=row["exchange_token"],
            isin=row.get("isin") or None,
            name=row.get("name") or None,
            is_index=row["instrument_type"] == "IDX",
            series=row.get("series") or None,
            is_intraday={"1": True, "0": False}.get((row.get("is_intraday") or "").strip()),
        )
