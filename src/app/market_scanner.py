"""Market-wide volume scanner service — M11 (DECISIONS #21).

1. `prepare(today)`: daily candles for every scan-universe stock (one call
   each, before the session; cached per day) → 20-day stats → the liquid
   pool (existing universe.yaml filters).
2. Sweep: quotes the pool round-robin at `calls_per_minute` in a
   background thread (quotes carry today's cumulative volume; the feed
   and batch OHLC do not — verified). Latest quote per stock is kept.
3. `ranked(now)`: top stocks by volume change (volume_scan). The worker
   turns that into the active universe and `record()`s each minute's scan
   to data/scans/<date>.jsonl, building an honest history for M10.
Read-only market data; one failed quote is skipped, never guessed.
"""

from __future__ import annotations

import json
import threading
import time as _time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from src.quantitative.volume_scan import (
    DailyStats,
    ScanFilters,
    daily_stats,
    rank_volume_change,
)

STATS_LOOKBACK_DAYS = 60          # calendar days fetched to get 20 sessions (< 180 limit)


@dataclass(frozen=True)
class ScannerConfig:
    top_n: int = 25
    min_stay_minutes: float = 10
    calls_per_minute: float = 200
    stats_sessions: int = 20

    @classmethod
    def from_dict(cls, d: dict) -> ScannerConfig:
        return cls(int(d.get("top_n", 25)), float(d.get("min_stay_minutes", 10)),
                   float(d.get("calls_per_minute", 200)), int(d.get("stats_sessions", 20)))


class MarketScanner:
    def __init__(self, adapter, instruments: list, filters: ScanFilters, cfg: ScannerConfig,
                 curve: list[float], cache_dir: str | Path, scans_dir: str | Path) -> None:
        self.adapter, self.filters, self.cfg, self.curve = adapter, filters, cfg, curve
        self.instruments = {i.trading_symbol: i for i in instruments}
        self.cache_dir, self.scans_dir = Path(cache_dir), Path(scans_dir)
        self.stats: dict[str, DailyStats] = {}
        self.pool: list[str] = []
        self._quotes: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cursor = 0
        self._calls = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_errors: list[str] = []

    # -- morning prep ---------------------------------------------------------

    def prepare(self, today: date, progress=None) -> list[str]:
        cache = self.cache_dir / f"daily_stats_{today.isoformat()}.json"
        errors: list[str] = []
        if cache.exists():
            raw = json.loads(cache.read_text(encoding="utf-8"))
            self.stats = {k: DailyStats(**v) for k, v in raw.items()}
        else:
            end = today - timedelta(days=1)
            for n, (sym, inst) in enumerate(sorted(self.instruments.items()), 1):
                try:
                    bars = self.adapter.get_daily_candles(
                        inst, today - timedelta(days=STATS_LOOKBACK_DAYS), end)
                    st = daily_stats(sym, bars, today, self.cfg.stats_sessions)
                    if st is not None:
                        self.stats[sym] = st
                except Exception as exc:          # one bad symbol never stops the prep
                    errors.append(f"daily {sym}: {type(exc).__name__}: {exc}")
                if progress and n % 100 == 0:
                    progress(n, len(self.instruments))
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({k: asdict(v) for k, v in self.stats.items()}),
                             encoding="utf-8")
        f = self.filters
        self.pool = sorted(s for s, st in self.stats.items()
                           if s in self.instruments and st.prev_close >= f.min_price
                           and st.avg_volume >= f.min_avg_daily_volume
                           and st.avg_traded_value >= f.min_avg_traded_value)
        return errors

    # -- sweep ------------------------------------------------------------------

    def sweep_step(self, n: int, now: datetime) -> list[str]:
        errors = []
        for _ in range(n):
            if not self.pool:
                break
            sym = self.pool[self._cursor % len(self.pool)]
            self._cursor += 1
            self._calls += 1
            try:
                q = self.adapter.get_quote(self.instruments[sym])
                with self._lock:
                    self._quotes[sym] = {"symbol": sym, "volume": int(q.volume),
                                         "last_price": float(q.last_price), "at": now}
            except Exception as exc:
                errors.append(f"scan {sym}: {type(exc).__name__}: {exc}")
        return errors

    def _loop(self) -> None:
        pause = 60.0 / self.cfg.calls_per_minute
        while not self._stop.is_set():
            started = _time.monotonic()
            errs = self.sweep_step(1, datetime.now())
            if errs:
                self.last_errors = (self.last_errors + errs)[-20:]
            self._stop.wait(max(0.0, pause - (_time.monotonic() - started)))

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="volume-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- results ----------------------------------------------------------------

    def ranked(self, now: datetime):
        with self._lock:
            quotes = list(self._quotes.values())
        return rank_volume_change(quotes, self.stats, self.filters, now.time(), self.curve)

    def status(self) -> dict:
        with self._lock:
            quoted = len(self._quotes)
        return {"pool": len(self.pool), "quoted": quoted,
                "full_sweeps": self._calls // len(self.pool) if self.pool else 0,
                "universe": len(self.instruments), "stats": len(self.stats)}

    def record(self, now: datetime, active: list[str]) -> None:
        top = [{"symbol": c.symbol, "volume_change": round(c.volume_change, 4),
                "day_change_pct": round(c.day_change_pct, 4), "volume": c.volume}
               for c in self.ranked(now)[: self.cfg.top_n]]
        path = self.scans_dir / f"{now.date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now.isoformat(), "active": active, "top": top,
                                **self.status()}) + "\n")
