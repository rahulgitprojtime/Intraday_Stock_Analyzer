"""Groww implementation of BrokerAdapter.

STUB — implementation is scheduled for M1 (see TODO.md). Every method
currently raises NotImplementedError rather than faking a response, per the
project rule against fabricating market data or API behavior.

When implementing:
- Re-verify method signatures against https://groww.in/trade-api/docs first
  (docs/groww_api_notes.md is a snapshot, not guaranteed current).
- Auth flow is selected via GROWW_AUTH_MODE env var ("api_key" or "totp").
- Respect per-type rate limits (Authentication/Orders/Live Data/Non-Trading)
  — add retry/backoff via `tenacity`, not bespoke loops.
- Historical candle requests must be chunked to the interval's max window
  (table in docs/groww_api_notes.md) and results stitched together.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence

from src.broker.base import AuthenticationError, BrokerAdapter
from src.data.models import (
    Candle,
    HistoricalCandleRequest,
    Instrument,
    MarketDepth,
    OHLC,
    Quote,
)


class GrowwAdapter(BrokerAdapter):
    def __init__(self) -> None:
        self._authenticated = False
        auth_mode = os.getenv("GROWW_AUTH_MODE", "api_key")
        if auth_mode not in ("api_key", "totp"):
            raise ValueError(f"Unknown GROWW_AUTH_MODE: {auth_mode!r}")
        self._auth_mode = auth_mode

    def authenticate(self) -> None:
        # M1: call GrowwAPI.get_access_token(...) per self._auth_mode,
        # then instantiate GrowwAPI(access_token). Raise AuthenticationError
        # on failure rather than leaving is_authenticated ambiguous.
        raise NotImplementedError("Groww auth is implemented in M1")

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def resolve_instrument(self, trading_symbol: str, exchange: str, segment: str) -> Instrument:
        raise NotImplementedError("Instrument resolution is implemented in M2")

    def get_quote(self, instrument: Instrument) -> Quote:
        raise NotImplementedError("Implemented in M1")

    def get_ltp(self, instruments: Sequence[Instrument]) -> dict[str, float]:
        raise NotImplementedError("Implemented in M1")

    def get_ohlc(self, instruments: Sequence[Instrument]) -> dict[str, OHLC]:
        raise NotImplementedError("Implemented in M1")

    def get_historical_candles(self, request: HistoricalCandleRequest) -> list[Candle]:
        raise NotImplementedError("Implemented in M1")

    def subscribe_ltp(
        self,
        instruments: Sequence[Instrument],
        on_data: Callable[[dict], None],
    ) -> None:
        raise NotImplementedError("Implemented in M3 (live feed)")

    def unsubscribe_ltp(self, instruments: Sequence[Instrument]) -> None:
        raise NotImplementedError("Implemented in M3 (live feed)")

    def get_market_depth(self, instrument: Instrument) -> MarketDepth:
        raise NotImplementedError("Implemented in M3 (live feed)")

    def connection_state(self) -> str:
        raise NotImplementedError("Implemented in M3 (live feed)")
