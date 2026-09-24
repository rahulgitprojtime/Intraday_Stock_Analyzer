"""Broker-agnostic data models.

Every layer above `src/broker/` depends only on these types, never on
Groww-specific response shapes. This is what keeps the broker swappable
(Phase 20) and the rest of the codebase testable without live API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class Segment(str, Enum):
    """Cash equity only — F&O is out of scope for this project (see
    DECISIONS.md #6). Not modeling FNO here at all rather than adding an
    unused member, per the project's own "don't over-engineer" rule."""

    CASH = "CASH"


@dataclass(frozen=True)
class Instrument:
    """A tradable instrument, broker-agnostic."""

    trading_symbol: str
    exchange: Exchange
    segment: Segment
    exchange_token: str | None = None   # required for live feed subscriptions
    isin: str | None = None
    name: str | None = None             # company / index display name
    is_index: bool = False              # e.g. NIFTY, BANKNIFTY — market context only
    series: str | None = None           # exchange series, e.g. NSE "EQ", "BE"
    is_intraday: bool | None = None     # MIS (intraday) allowed; None = unknown


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class MarketDepth:
    buy: tuple[DepthLevel, ...] = ()
    sell: tuple[DepthLevel, ...] = ()
    as_of: datetime | None = None


@dataclass(frozen=True)
class Quote:
    """Snapshot quote for an instrument."""

    instrument: Instrument
    last_price: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    day_change: float
    day_change_pct: float
    depth: MarketDepth | None = None
    as_of: datetime | None = None


@dataclass(frozen=True)
class OHLC:
    instrument: Instrument
    open: float
    high: float
    low: float
    close: float
    as_of: datetime | None = None


@dataclass(frozen=True)
class Candle:
    """A single OHLCV candle for a given timeframe."""

    instrument: Instrument
    timeframe_minutes: int
    timestamp: datetime          # candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_complete: bool = True     # False while a candle is still forming


@dataclass(frozen=True)
class HistoricalCandleRequest:
    instrument: Instrument
    start_time: datetime
    end_time: datetime
    interval_minutes: int


class ConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"
    STALE = "STALE"              # connected but data hasn't updated recently


@dataclass
class FeedHealth:
    state: ConnectionState
    last_tick_at: datetime | None = None
    stale_symbols: list[str] = field(default_factory=list)
    last_error: str | None = None
