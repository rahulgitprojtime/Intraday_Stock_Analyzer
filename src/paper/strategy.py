"""Strategy base class — DECISIONS #29. SIMULATION ONLY.

A strategy sees the market only through `StrategyContext`: today's CLOSED
1-min bars and the broker. The same class runs unchanged in a backtest
(bars from the cache) and in paper mode (bars from the live worker, fills
from live ticks); only the session driving it differs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from src.paper.orders import Bar, Fill, Position


class StrategyContext:
    def __init__(self, broker) -> None:
        self.broker = broker
        self.as_of: datetime | None = None
        self.day: date | None = None
        self._history: dict[str, list[Bar]] = {}

    def bars(self, symbol: str) -> list[Bar]:
        """Today's closed bars for `symbol`, oldest first (a copy)."""
        return list(self._history.get(symbol, ()))

    def symbols(self) -> list[str]:
        return sorted(self._history)

    def position(self, symbol: str) -> Position | None:
        return self.broker.positions().get(symbol)

    def _reset(self, day: date) -> None:
        self.day, self.as_of, self._history = day, None, {}

    def _append(self, bar: Bar) -> None:
        self._history.setdefault(bar.symbol, []).append(bar)


class Strategy(ABC):
    name = "STRATEGY"

    def params(self) -> dict:
        return {}

    def on_day_start(self, day: date, ctx: StrategyContext) -> None:
        pass

    @abstractmethod
    def on_bars(self, as_of: datetime, bars: dict[str, Bar], ctx: StrategyContext) -> None:
        """`bars`: the bars that closed at `as_of` (latest per symbol). Orders
        placed here can fill on the next market event at the earliest."""

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        pass

    def on_day_end(self, day: date, ctx: StrategyContext) -> None:
        pass
