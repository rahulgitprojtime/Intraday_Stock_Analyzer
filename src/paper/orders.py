"""Simulated order/fill/position types — DECISIONS #29. SIMULATION ONLY.

Nothing here is sent anywhere: these records exist inside the simulated
brokers and the SQLite ledger. `Bar` is the one market event the brokers
understand; a live tick is a bar with open = high = low = close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.paper.costs import Charges


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"          # stop-market: triggers at trigger_price, fills like a market order


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime                 # bar open time (a tick: its time)
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @classmethod
    def from_candle(cls, c) -> Bar:
        return cls(c.instrument.trading_symbol, c.timestamp, c.open, c.high, c.low, c.close,
                   c.volume)

    @classmethod
    def tick(cls, symbol: str, ts: datetime, price: float) -> Bar:
        return cls(symbol, ts, price, price, price, price)


@dataclass(frozen=True)
class Bracket:
    """Exit orders attached to a BUY entry, placed when it fills: a STOP sell
    `stop` below the fill and, optionally, a LIMIT sell `target` above it.
    Distances are rupees, or % of the fill when `pct` is true. The two exits
    are one-cancels-the-other."""

    stop: float
    target: float | None = None
    pct: bool = False

    def levels(self, fill: float) -> tuple[float, float | None]:
        k = fill / 100 if self.pct else 1.0
        return (round(fill - self.stop * k, 4),
                None if self.target is None else round(fill + self.target * k, 4))


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    quantity: int
    type: OrderType
    placed_at: datetime
    limit_price: float | None = None
    trigger_price: float | None = None
    tag: str = ""
    bracket: Bracket | None = None
    parent_id: str | None = None
    oco_id: str | None = None
    exchange: str = "NSE"
    status: OrderStatus = OrderStatus.OPEN
    reason: str | None = None
    fill_price: float | None = None
    filled_at: datetime | None = None
    active_seq: int = 0          # first market event this order may fill on (no look-ahead)


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    at: datetime
    charges: Charges
    tag: str = ""
    note: str | None = None


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_price: float = 0.0
    realized_pnl: float = 0.0    # gross, before charges
    charges: float = 0.0
    last_price: float | None = None

    @property
    def unrealized_pnl(self) -> float:
        if not self.quantity or self.last_price is None:
            return 0.0
        return (self.last_price - self.avg_price) * self.quantity
