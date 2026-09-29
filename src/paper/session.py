"""Trading session driver — DECISIONS #29. SIMULATION ONLY.

Wires one strategy to one simulated broker on a minute clock. Each
`step(as_of, bars)` receives the bars that CLOSED at `as_of`:

1. backtest (`fill_on_bars=True`): each new bar is first offered to the
   broker, so orders placed on earlier minutes fill at its open;
   paper (`fill_on_bars=False`): live ticks fill orders via `on_tick`,
   bars only update the strategy's history;
2. at/after the broker's square-off time everything is closed at the last
   price and the strategy is not called again that day;
3. otherwise the strategy sees the new bars and may place orders, which
   can only fill on a later event.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from src.paper.ledger import daily_row
from src.paper.orders import Bar
from src.paper.strategy import Strategy, StrategyContext


class TradingSession:
    def __init__(self, strategy: Strategy, broker, *, ledger=None, fill_on_bars: bool = True):
        self.strategy, self.broker, self.ledger = strategy, broker, ledger
        self.fill_on_bars = fill_on_bars
        self.ctx = StrategyContext(broker)
        self.equity_curve: list[tuple[datetime, float]] = []
        self.daily: list[dict] = []
        self._squared = False
        broker.add_fill_listener(lambda f: strategy.on_fill(f, self.ctx))

    def start_day(self, day: date) -> None:
        self.ctx._reset(day)
        self._squared = False
        self.strategy.on_day_start(day, self.ctx)

    def on_tick(self, event) -> list:
        """Live tick (paper mode)."""
        return self.broker.on_tick(event)

    def step(self, as_of: datetime, bars: Iterable[Bar]) -> None:
        latest: dict[str, Bar] = {}
        for b in sorted(bars, key=lambda b: (b.ts, b.symbol)):
            if self.fill_on_bars:
                self.broker.on_tick(b)
            elif self.broker.last_price(b.symbol) is None:
                self.broker.mark(b.symbol, b.close)    # paper: until the first live tick
            self.ctx._append(b)
            latest[b.symbol] = b
        self.ctx.as_of = as_of
        if as_of.time() >= self.broker.cfg.square_off:
            if not self._squared:
                self.broker.square_off(as_of)
                self._squared = True
        else:
            self.strategy.on_bars(as_of, latest, self.ctx)
        self._record_equity(as_of)

    def end_day(self, day: date, at: datetime | None = None) -> dict:
        at = at or self.ctx.as_of or datetime.combine(day, self.broker.cfg.square_off)
        if self.broker.positions() or self.broker.open_orders():   # data ended before square-off
            self.broker.square_off(at)
        self._squared = True
        self.strategy.on_day_end(day, self.ctx)
        row = daily_row(day, self.broker.trades, self.broker.equity())
        self.daily.append(row)
        if self.ledger is not None:
            self.ledger.record_day(self.broker.run_id, day, self.broker.trades,
                                   self.broker.equity())
            self.ledger.commit()
        return row

    def _record_equity(self, as_of: datetime) -> None:
        eq = round(self.broker.equity(), 4)
        self.equity_curve.append((as_of, eq))
        if self.ledger is not None:
            self.ledger.record_equity(self.broker.run_id, as_of, eq)
            self.broker.persist_positions(as_of)
            if not self.fill_on_bars:          # paper: the dashboard reads it live
                self.ledger.commit()
