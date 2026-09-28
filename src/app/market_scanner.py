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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from src.quantitative.volume_scan import (
    DailyStats,
    ScanFilters,
    daily_stats,
    prescore,
    rank_volume_change,
)
from src.utils.ratelimit import RateLimiter

STATS_LOOKBACK_DAYS = 60          # calendar days fetched to get 20 sessions (< 180 limit)


@dataclass(frozen=True)
class ScannerConfig:
    top_n: int = 25
    min_stay_minutes: float = 10
    calls_per_minute: float = 200
    stats_sessions: int = 20
    prep_calls_per_minute: float = 150
    movers_per_cycle: int = 100          # M13: quoted for volume each minute
    quote_workers: int = 4
    max_calls_per_second: float = 8      # Groww Live Data limit is 10/s
    quote_max_age_seconds: float = 180   # older quotes stop counting

    @classmethod
    def from_dict(cls, d: dict) -> ScannerConfig:
        return cls(int(d.get("top_n", 25)), float(d.get("min_stay_minutes", 10)),
                   float(d.get("calls_per_minute", 200)), int(d.get("stats_sessions", 20)),
                   float(d.get("prep_calls_per_minute", 150)),
                   int(d.get("movers_per_cycle", 100)), int(d.get("quote_workers", 4)),
                   float(d.get("max_calls_per_second", 8)),
                   float(d.get("quote_max_age_seconds", 180)))


class MarketScanner:
    def __init__(self, adapter, instruments: list, filters: ScanFilters, cfg: ScannerConfig,
                 curve: list[float], cache_dir: str | Path, scans_dir: str | Path,
                 sleep=_time.sleep, ltp_source: Callable[[], dict] | None = None) -> None:
        self.adapter, self.filters, self.cfg, self.curve = adapter, filters, cfg, curve
        self._sleep = sleep
        self.ltp_source = ltp_source            # live feed prices for the pool (M13)
        self._limiter = RateLimiter(cfg.max_calls_per_second)
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
        """Incremental and paced. The day's cache holds each stock's stats,
        or null for "no history" (e.g. a new listing); a *failed* fetch is
        never cached, so a rerun fetches only what is missing (2026-09-28:
        a bad run must not poison the day)."""
        cache = self.cache_dir / f"daily_stats_{today.isoformat()}.json"
        known: dict = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        todo = [s for s in sorted(self.instruments) if s not in known]
        errors: list[str] = []
        end = today - timedelta(days=1)
        gap = 60.0 / self.cfg.prep_calls_per_minute
        for n, sym in enumerate(todo, 1):
            started = _time.monotonic()
            try:
                bars = self.adapter.get_daily_candles(
                    self.instruments[sym], today - timedelta(days=STATS_LOOKBACK_DAYS), end)
                st = daily_stats(sym, bars, today, self.cfg.stats_sessions)
                known[sym] = asdict(st) if st is not None else None
            except Exception as exc:              # one bad symbol never stops the prep
                errors.append(f"daily {sym}: {type(exc).__name__}: {exc}")
            if progress and n % 100 == 0:
                progress(n, len(todo))
            self._sleep(max(0.0, gap - (_time.monotonic() - started)))
        if todo:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        self.stats = {k: DailyStats(**v) for k, v in known.items() if v}
        f = self.filters
        self.pool = sorted(s for s, st in self.stats.items()
                           if s in self.instruments and f.price_ok(st.prev_close)
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
                    prev = self._quotes.get(sym)
                    self._quotes[sym] = {
                        "symbol": sym, "volume": int(q.volume), "last_price": float(q.last_price),
                        "open": q.open or None, "high": q.high or None, "low": q.low or None,
                        "average_price": getattr(q, "average_price", None), "at": now,
                        "prev_volume": prev["volume"] if prev else None,
                        "prev_at": prev["at"] if prev else None}
            except Exception as exc:
                errors.append(f"scan {sym}: {type(exc).__name__}: {exc}")
        return errors

    def _quote_one(self, sym: str, now: datetime) -> str | None:
        self._limiter.acquire()
        try:
            q = self.adapter.get_quote(self.instruments[sym])
        except Exception as exc:
            return f"scan {sym}: {type(exc).__name__}: {exc}"
        with self._lock:
            prev = self._quotes.get(sym)
            self._quotes[sym] = {
                "symbol": sym, "volume": int(q.volume), "last_price": float(q.last_price),
                "open": q.open or None, "high": q.high or None, "low": q.low or None,
                "average_price": getattr(q, "average_price", None), "at": now,
                "prev_volume": prev["volume"] if prev else None,
                "prev_at": prev["at"] if prev else None}
        return None

    def cycle(self, now: datetime, ltp: dict | None = None) -> list[str]:
        """One fast pass (M13, DECISIONS #23): day OHLC for the whole pool in
        batches of 50, prices from the live feed (batch LTP for pool stocks
        the feed does not cover), movement pre-rank of every pool stock, then parallel quotes
        (volume, VWAP) for only the top `movers_per_cycle` movers."""
        errors: list[str] = []
        insts = [self.instruments[s] for s in self.pool]
        try:
            ohlc = self.adapter.get_ohlc(insts)
        except Exception as exc:
            ohlc = {}
            errors.append(f"scan ohlc: {type(exc).__name__}: {exc}")
        ltp = dict(ltp or {})
        missing = [i for i in insts if ltp.get(i.trading_symbol) is None]   # feed covers the active set only
        if missing:
            try:
                ltp.update(self.adapter.get_ltp(missing))
            except Exception as exc:
                errors.append(f"scan ltp: {type(exc).__name__}: {exc}")
        moving = []
        for sym in self.pool:
            last, o, st = ltp.get(sym), ohlc.get(sym), self.stats.get(sym)
            if last is None or o is None or st is None:
                continue
            if self.filters.long_only and last <= st.prev_close:
                continue
            q = {"symbol": sym, "volume": 0, "last_price": last, "open": o.open,
                 "high": max(o.high, last), "low": min(o.low, last), "at": now}
            parts = prescore(q, st, self.curve)[1]["movement"]
            have = [v for v in parts.values() if v is not None]
            moving.append((sum(have) / len(have) if have else 0.0, sym))
        top = [sym for _, sym in sorted(moving, key=lambda m: (-m[0], m[1]))]
        top = top[: self.cfg.movers_per_cycle]
        with ThreadPoolExecutor(max_workers=self.cfg.quote_workers) as ex:
            errors += [e for e in ex.map(lambda s: self._quote_one(s, now), top) if e]
        self._calls += len(top)
        return errors

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = _time.monotonic()
            try:
                ltp = self.ltp_source() if self.ltp_source else None
                errs = self.cycle(datetime.now(), ltp)
            except Exception as exc:               # the scan must never kill the worker
                errs = [f"scan cycle: {type(exc).__name__}: {exc}"]
            if errs:
                self.last_errors = (self.last_errors + errs)[-20:]
            self._stop.wait(max(0.0, 60.0 - (_time.monotonic() - started)))

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
        max_age = self.cfg.quote_max_age_seconds
        with self._lock:
            quotes = [q for q in self._quotes.values()
                      if (now - q["at"]).total_seconds() <= max_age]
        return rank_volume_change(quotes, self.stats, self.filters, now.time(), self.curve)

    def status(self) -> dict:
        with self._lock:
            quoted = len(self._quotes)
        return {"pool": len(self.pool), "quoted": quoted,
                "full_sweeps": self._calls // len(self.pool) if self.pool else 0,
                "universe": len(self.instruments), "stats": len(self.stats)}

    def record(self, now: datetime, active: list[str]) -> None:
        top = [{"symbol": c.symbol, "scan_score": round(c.score, 2),
                "volume_change": round(c.volume_change, 4),
                "day_change_pct": round(c.day_change_pct, 4), "volume": c.volume}
               for c in self.ranked(now)[: self.cfg.top_n]]
        path = self.scans_dir / f"{now.date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now.isoformat(), "active": active, "top": top,
                                **self.status()}) + "\n")
