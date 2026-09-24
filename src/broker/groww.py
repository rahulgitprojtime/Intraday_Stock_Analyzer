"""Groww implementation of BrokerAdapter — M1.

Scope for this file (see TODO.md M1): authentication (both flows), and the
snapshot/historical read methods (get_quote, get_ltp, get_ohlc,
get_historical_candles). Streaming (subscribe_ltp, get_market_depth,
connection_state) is M3 and stays NotImplementedError here.

Cash equity only — F&O is out of scope (DECISIONS.md #6). Every method
assumes `Segment.CASH`.

`growwapi` (and `pyotp`, for TOTP auth) are imported defensively: if they
aren't installed, the adapter still imports cleanly and raises a clear
`BrokerAdapterError` the first time it's actually used, rather than
crashing at import time. This also means the module's own unit tests don't
require the real package — they monkeypatch `GrowwAPI` / `pyotp` here with
fakes. Never treat the fallback placeholder exception classes below as a
substitute for the real thing in production; they exist only so
`except GrowwAPIRateLimitException` etc. remains valid Python when the real
package isn't present.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from src.broker.base import (
    AuthenticationError,
    BrokerAdapter,
    BrokerAdapterError,
    RateLimitError,
)
from src.data.models import (
    Candle,
    DepthLevel,
    Exchange,
    HistoricalCandleRequest,
    Instrument,
    MarketDepth,
    OHLC,
    Quote,
    Segment,
)
from src.utils.retry import retry_call

try:
    from growwapi import GrowwAPI
    from growwapi.groww.exceptions import (
        GrowwAPIAuthenticationException,
        GrowwAPIAuthorisationException,
        GrowwAPIBadRequestException,
        GrowwAPIException,
        GrowwAPINotFoundException,
        GrowwAPIRateLimitException,
        GrowwAPITimeoutException,
        GrowwBaseException,
    )

    GROWWAPI_INSTALLED = True
except ImportError:  # pragma: no cover - exercised implicitly whenever growwapi isn't installed
    GrowwAPI = None
    GROWWAPI_INSTALLED = False

    class GrowwBaseException(Exception):
        pass

    class GrowwAPIException(GrowwBaseException):
        pass

    class GrowwAPIAuthenticationException(GrowwAPIException):
        pass

    class GrowwAPIAuthorisationException(GrowwAPIException):
        pass

    class GrowwAPIBadRequestException(GrowwAPIException):
        pass

    class GrowwAPINotFoundException(GrowwAPIException):
        pass

    class GrowwAPIRateLimitException(GrowwAPIException):
        pass

    class GrowwAPITimeoutException(GrowwAPIException):
        pass

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None


# Candle interval (minutes) -> GrowwAPI SDK constant attribute name.
# Verified against docs/groww_api_notes.md. Only the intervals this project
# actually uses (config/settings.yaml: 1/3/5/15 min) plus a few obvious
# neighbors — not the full Annexures list, since day/week/month constants
# weren't verified verbatim and aren't needed yet.
_CANDLE_INTERVAL_ATTR: dict[int, str] = {
    1: "CANDLE_INTERVAL_MIN_1",
    2: "CANDLE_INTERVAL_MIN_2",
    3: "CANDLE_INTERVAL_MIN_3",
    5: "CANDLE_INTERVAL_MIN_5",
    10: "CANDLE_INTERVAL_MIN_10",
    15: "CANDLE_INTERVAL_MIN_15",
    30: "CANDLE_INTERVAL_MIN_30",
    60: "CANDLE_INTERVAL_HOUR_1",
    240: "CANDLE_INTERVAL_HOUR_4",
}

# Max request window (days) per interval, per Groww's Backtesting Data
# Limits table (docs/groww_api_notes.md).
_MAX_WINDOW_DAYS: dict[int, int] = {
    1: 30, 2: 30, 3: 30, 5: 30,
    10: 90, 15: 90, 30: 90,
    60: 180, 240: 180,
}

_RETRYABLE = (RateLimitError, GrowwAPITimeoutException)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise AuthenticationError(f"Required environment variable {name} is not set.")
    return value


class GrowwAdapter(BrokerAdapter):
    def __init__(self) -> None:
        self._authenticated = False
        self._client = None  # set on successful authenticate()
        auth_mode = os.getenv("GROWW_AUTH_MODE", "api_key")
        if auth_mode not in ("api_key", "totp"):
            raise ValueError(f"Unknown GROWW_AUTH_MODE: {auth_mode!r}")
        self._auth_mode = auth_mode

    # -- Auth --------------------------------------------------------

    def authenticate(self) -> None:
        if GrowwAPI is None:
            raise BrokerAdapterError(
                "The 'growwapi' package is not installed. Run: pip install growwapi"
            )
        try:
            if self._auth_mode == "api_key":
                access_token = GrowwAPI.get_access_token(
                    api_key=_require_env("GROWW_API_KEY"),
                    secret=_require_env("GROWW_API_SECRET"),
                )
            else:
                if pyotp is None:
                    raise BrokerAdapterError(
                        "GROWW_AUTH_MODE=totp requires the 'pyotp' package. Run: pip install pyotp"
                    )
                totp_secret = _require_env("GROWW_TOTP_SECRET")
                totp_token = _require_env("GROWW_TOTP_TOKEN")
                access_token = GrowwAPI.get_access_token(
                    api_key=totp_token,
                    totp=pyotp.TOTP(totp_secret).now(),
                )
            self._client = GrowwAPI(access_token)
            self._authenticated = True
        except (GrowwAPIAuthenticationException, GrowwAPIAuthorisationException) as exc:
            self._authenticated = False
            raise AuthenticationError(str(exc)) from exc
        except GrowwBaseException as exc:
            self._authenticated = False
            raise BrokerAdapterError(str(exc)) from exc

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def _ensure_authenticated(self) -> None:
        if not self._authenticated or self._client is None:
            raise AuthenticationError(
                "GrowwAdapter.authenticate() must succeed before making API calls."
            )

    def _call(self, fn: Callable, /, **kwargs):
        """Invoke an SDK method, translating growwapi exceptions into this
        project's own exception types and retrying rate-limit/timeout
        errors with backoff (src/utils/retry.py)."""

        def _attempt():
            try:
                return fn(**kwargs)
            except (GrowwAPIAuthenticationException, GrowwAPIAuthorisationException) as exc:
                raise AuthenticationError(str(exc)) from exc
            except GrowwAPIRateLimitException as exc:
                raise RateLimitError(str(exc)) from exc
            except (GrowwAPIBadRequestException, GrowwAPINotFoundException) as exc:
                raise BrokerAdapterError(str(exc)) from exc
            except GrowwBaseException as exc:
                raise BrokerAdapterError(str(exc)) from exc

        return retry_call(_attempt, retry_on=_RETRYABLE, max_attempts=4, base_delay_seconds=1.0)

    # -- Instrument discovery -----------------------------------------

    def resolve_instrument(self, trading_symbol: str, exchange: str, segment: str) -> Instrument:
        try:
            exch = Exchange(exchange)
        except ValueError as exc:
            raise ValueError(f"Unsupported exchange: {exchange!r}") from exc
        if segment != Segment.CASH.value:
            raise ValueError(
                f"Unsupported segment: {segment!r}. Only CASH is supported — "
                "F&O is out of scope for this project (DECISIONS.md #6)."
            )
        # Exchange-token lookup against the instrument master is M2; a
        # trading_symbol/exchange/segment triple is enough for get_quote,
        # get_ltp, get_ohlc, and get_historical_candles.
        return Instrument(trading_symbol=trading_symbol, exchange=exch, segment=Segment.CASH)

    # -- Symbol formatting helpers --------------------------------------

    @staticmethod
    def _exchange_symbol(instrument: Instrument) -> str:
        """Format used by get_ltp/get_ohlc, e.g. 'NSE_RELIANCE'."""
        return f"{instrument.exchange.value}_{instrument.trading_symbol}"

    @staticmethod
    def _groww_symbol(instrument: Instrument) -> str:
        """Format used by get_historical_candles, e.g. 'NSE-RELIANCE'."""
        return f"{instrument.exchange.value}-{instrument.trading_symbol}"

    @staticmethod
    def _chunk(items: Sequence, size: int):
        for i in range(0, len(items), size):
            yield items[i : i + size]

    @staticmethod
    def _parse_candle_timestamp(raw: str) -> datetime:
        # Docs' schema table says "yyyy-MM-dd HH:mm:ss"; the docs' own
        # example response uses a "T" separator instead. Handle both.
        return datetime.strptime(raw.replace("T", " "), "%Y-%m-%d %H:%M:%S")

    def _exchange_const(self, instrument: Instrument):
        return getattr(self._client, f"EXCHANGE_{instrument.exchange.value}")

    def _segment_const(self, instrument: Instrument):
        return getattr(self._client, f"SEGMENT_{instrument.segment.value}")

    # -- Live snapshot data --------------------------------------------

    def get_quote(self, instrument: Instrument) -> Quote:
        self._ensure_authenticated()
        resp = self._call(
            self._client.get_quote,
            exchange=self._exchange_const(instrument),
            segment=self._segment_const(instrument),
            trading_symbol=instrument.trading_symbol,
        )

        depth = None
        raw_depth = resp.get("depth")
        if raw_depth:
            depth = MarketDepth(
                buy=tuple(
                    DepthLevel(price=lvl["price"], quantity=lvl["quantity"])
                    for lvl in raw_depth.get("buy", [])
                ),
                sell=tuple(
                    DepthLevel(price=lvl["price"], quantity=lvl["quantity"])
                    for lvl in raw_depth.get("sell", [])
                ),
            )

        as_of = None
        last_trade_time = resp.get("last_trade_time")
        if last_trade_time:
            as_of = datetime.fromtimestamp(last_trade_time / 1000, tz=timezone.utc)

        ohlc = resp.get("ohlc", {})
        return Quote(
            instrument=instrument,
            last_price=resp["last_price"],
            open=ohlc.get("open", 0.0),
            high=ohlc.get("high", 0.0),
            low=ohlc.get("low", 0.0),
            close=ohlc.get("close", 0.0),
            volume=resp.get("volume", 0),
            day_change=resp.get("day_change", 0.0),
            day_change_pct=resp.get("day_change_perc", 0.0),
            depth=depth,
            as_of=as_of,
        )

    def get_ltp(self, instruments: Sequence[Instrument]) -> dict[str, float]:
        self._ensure_authenticated()
        instruments = list(instruments)
        result: dict[str, float] = {}
        for chunk in self._chunk(instruments, 50):
            key_to_symbol = {self._exchange_symbol(i): i.trading_symbol for i in chunk}
            resp = self._call(
                self._client.get_ltp,
                segment=self._segment_const(chunk[0]),
                exchange_trading_symbols=tuple(key_to_symbol.keys()),
            )
            for key, ltp in resp.items():
                result[key_to_symbol.get(key, key)] = ltp
        return result

    def get_ohlc(self, instruments: Sequence[Instrument]) -> dict[str, OHLC]:
        self._ensure_authenticated()
        instruments = list(instruments)
        result: dict[str, OHLC] = {}
        by_symbol = {self._exchange_symbol(i): i for i in instruments}
        for chunk in self._chunk(instruments, 50):
            keys = [self._exchange_symbol(i) for i in chunk]
            resp = self._call(
                self._client.get_ohlc,
                segment=self._segment_const(chunk[0]),
                exchange_trading_symbols=tuple(keys),
            )
            for key, payload in resp.items():
                instrument = by_symbol.get(key)
                if instrument is None:
                    continue
                result[instrument.trading_symbol] = OHLC(
                    instrument=instrument,
                    open=payload["open"],
                    high=payload["high"],
                    low=payload["low"],
                    close=payload["close"],
                    as_of=None,  # get_ohlc is a real-time snapshot, no explicit timestamp field
                )
        return result

    # -- Historical data -------------------------------------------------

    @staticmethod
    def _split_window(
        start: datetime, end: datetime, max_days: int
    ) -> list[tuple[datetime, datetime]]:
        windows = []
        step = timedelta(days=max_days)
        cur = start
        while cur < end:
            win_end = min(cur + step, end)
            windows.append((cur, win_end))
            cur = win_end
        return windows or [(start, end)]

    def get_historical_candles(self, request: HistoricalCandleRequest) -> list[Candle]:
        self._ensure_authenticated()
        interval = request.interval_minutes
        if interval not in _CANDLE_INTERVAL_ATTR:
            raise ValueError(
                f"Unsupported interval_minutes={interval}. "
                f"Supported: {sorted(_CANDLE_INTERVAL_ATTR)}"
            )
        candle_interval = getattr(self._client, _CANDLE_INTERVAL_ATTR[interval])
        exchange_const = self._exchange_const(request.instrument)
        segment_const = self._segment_const(request.instrument)
        groww_symbol = self._groww_symbol(request.instrument)
        max_days = _MAX_WINDOW_DAYS[interval]

        candles: list[Candle] = []
        for win_start, win_end in self._split_window(request.start_time, request.end_time, max_days):
            resp = self._call(
                self._client.get_historical_candles,
                exchange=exchange_const,
                segment=segment_const,
                groww_symbol=groww_symbol,
                start_time=win_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=win_end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval=candle_interval,
            )
            for row in resp.get("candles", []):
                ts_raw, o, h, l, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
                candles.append(
                    Candle(
                        instrument=request.instrument,
                        timeframe_minutes=interval,
                        timestamp=self._parse_candle_timestamp(ts_raw),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=int(v),
                        is_complete=True,
                    )
                )

        # Window boundaries can overlap by one candle; de-dupe by timestamp
        # and return in order.
        deduped: dict[datetime, Candle] = {c.timestamp: c for c in candles}
        return [deduped[ts] for ts in sorted(deduped)]

    # -- Streaming (M3) ----------------------------------------------------

    def subscribe_ltp(
        self,
        instruments: Sequence[Instrument],
        on_data: Callable[[dict], None],
    ) -> None:
        raise NotImplementedError("Live feed is implemented in M3")

    def unsubscribe_ltp(self, instruments: Sequence[Instrument]) -> None:
        raise NotImplementedError("Live feed is implemented in M3")

    def get_market_depth(self, instrument: Instrument) -> MarketDepth:
        raise NotImplementedError("Live feed is implemented in M3")

    def connection_state(self) -> str:
        raise NotImplementedError("Live feed is implemented in M3")
