"""Dynamic universe — M11 (DECISIONS #21).

Each worker tick: the market scanner's ranking → ActiveSet (top N by
volume change, minimum stay, pinned symbols) → the worker's `ctx.stocks`.
New members get the existing prep (one 1-min history call each), the live
feed is resubscribed only when membership changes, and news learns their
names (curated news.yaml aliases first, else the Groww instrument name).
The scan is recorded every tick for later evaluation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime


class DynamicUniverse:
    def __init__(self, scanner, active_set, resolve: Callable, day: date, feed=None,
                 news_aliases: dict | None = None, pinned: Callable[[], set] = set) -> None:
        self.scanner, self.active_set, self.resolve, self.day = scanner, active_set, resolve, day
        self.feed, self.news_aliases, self.pinned = feed, news_aliases or {}, pinned
        self._instruments: dict = {}
        self._ranked: dict = {}

    def refresh(self, ctx, now: datetime) -> list[str]:
        ranked = self.scanner.ranked(now)
        self._ranked = {c.symbol: c for c in ranked}
        active = self.active_set.update(now, ranked, set(self.pinned()))
        errors: list[str] = []
        current = [i.trading_symbol for i in ctx.stocks]
        if set(active) != set(current):
            for sym in active:
                if sym in self._instruments:
                    continue
                try:
                    inst = self._instruments[sym] = self.resolve(sym)
                except Exception as exc:
                    errors.append(f"universe {sym}: {exc}")
                    continue
                try:
                    ctx.preps[sym] = ctx.source.prep(inst, self.day)
                except Exception as exc:        # scored as INCOMPLETE, never guessed
                    ctx.preps[sym] = None
                    errors.append(f"{sym}: prep failed: {exc}")
            ctx.stocks = [self._instruments[s] for s in active if s in self._instruments]
            if self.feed is not None:
                self.feed.set_stocks(ctx.stocks)
            if ctx.news is not None:
                ctx.news.add_symbols({i.trading_symbol: self.news_aliases.get(i.trading_symbol)
                                      or [i.name or i.trading_symbol] for i in ctx.stocks})
        else:
            ctx.stocks = [self._instruments[s] for s in active if s in self._instruments]
        self.scanner.record(now, [i.trading_symbol for i in ctx.stocks])
        return errors

    def block(self, ctx) -> dict:
        active = []
        for inst in ctx.stocks:
            c = self._ranked.get(inst.trading_symbol)
            active.append({"symbol": inst.trading_symbol,
                           "volume_change": round(c.volume_change, 4) if c else None,
                           "day_change_pct": round(c.day_change_pct, 4) if c else None})
        return {"source": "volume_scan", "top_n": self.active_set.top_n,
                **self.scanner.status(), "active": active}
