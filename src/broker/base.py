"""Broker-agnostic adapter interface.

`src/broker/groww.py` implements this against the `growwapi` SDK. No other
module in this codebase should import `growwapi` directly — everything
downstream talks to `BrokerAdapter` and the dataclasses in
`src/data/models.py`. This is the one interface Phase 20 (extensibility)
and Phase 3 (broker isolation) both call for explicitly; see DECISIONS.md #4
for why it's exempt from the "don't over-engineer" default.

Every method signature here is deliberately shaped after verified Groww SDK
capabilities (see docs/groww_api_notes.md) so the adapter is a thin,
faithful wrapper rather than a leaky abstraction invented in the abstract.

Market data only. This product recommends; it never trades — the
interface deliberately has no order/position/holdings methods
(DECISIONS.md #8). Do not add them without an explicit user request.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from src.data.models import (
    Candle,
    HistoricalCandleRequest,
    Instrument,
    MarketDepth,
    OHLC,
    Quote,
)


class BrokerAdapterError(Exception):
    """Base class for all broker adapter errors."""


class AuthenticationError(BrokerAdapterError):
    pass


class RateLimitError(BrokerAdapterError):
    """Raised when a call is rejected/should be retried due to rate limits."""


class StaleDataError(BrokerAdapterError):
    """Raised or signaled when returned data is detected as stale."""


class BrokerAdapter(ABC):
    """Abstract interface every broker integration must implement."""

    # -- Auth --------------------------------------------------------

    @abstractmethod
    def authenticate(self) -> None:
        """Establish/refresh a valid session. Idempotent — safe to call
        again before token expiry."""

    @property
    @abstractmethod
    def is_authenticated(self) -> bool: ...

    # -- Instrument discovery -----------------------------------------

    @abstractmethod
    def resolve_instrument(self, trading_symbol: str, exchange: str, segment: str) -> Instrument:
        """Resolve a human-entered symbol into a full Instrument, including
        exchange_token where required for feed subscriptions."""

    # -- Live snapshot data --------------------------------------------

    @abstractmethod
    def get_quote(self, instrument: Instrument) -> Quote: ...

    @abstractmethod
    def get_ltp(self, instruments: Sequence[Instrument]) -> dict[str, float]:
        """Up to 50 instruments per call (Groww limit) — adapter must chunk
        larger requests transparently."""

    @abstractmethod
    def get_ohlc(self, instruments: Sequence[Instrument]) -> dict[str, OHLC]: ...

    # -- Historical data -------------------------------------------------

    @abstractmethod
    def get_historical_candles(self, request: HistoricalCandleRequest) -> list[Candle]:
        """Adapter is responsible for chunking requests that exceed the
        per-interval max window (see docs/groww_api_notes.md) and stitching
        results back into one ordered list."""

    # -- Streaming -------------------------------------------------------

    @abstractmethod
    def subscribe_ltp(
        self,
        instruments: Sequence[Instrument],
        on_data: Callable[[dict], None],
    ) -> None:
        """Non-blocking subscribe; the adapter owns the background
        connection. Must never be called from the Streamlit process for
        its primary feed connection (see ARCHITECTURE.md)."""

    @abstractmethod
    def unsubscribe_ltp(self, instruments: Sequence[Instrument]) -> None: ...

    @abstractmethod
    def get_market_depth(self, instrument: Instrument) -> MarketDepth: ...

    @abstractmethod
    def connection_state(self) -> str:
        """Returns a ConnectionState value (see src/data/models.py)."""
