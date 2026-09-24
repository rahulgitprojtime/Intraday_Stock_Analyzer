from __future__ import annotations

from datetime import datetime

import pytest

from src.broker.groww import (
    GrowwAdapter,
    GrowwAPIAuthenticationException,
    GrowwAPIRateLimitException,
)
from src.data.models import Exchange, HistoricalCandleRequest, Instrument, Segment
from tests.fakes.fake_groww import FakePyotp, FakeTOTP, install_fake_groww


def _cash_instrument(symbol: str, exchange: Exchange = Exchange.NSE) -> Instrument:
    return Instrument(trading_symbol=symbol, exchange=exchange, segment=Segment.CASH)


# -- Authentication ------------------------------------------------------


def test_authenticate_api_key_flow_calls_sdk_with_key_and_secret(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    adapter = GrowwAdapter()
    adapter.authenticate()

    assert adapter.is_authenticated is True
    assert fake.access_token_calls == [{"api_key": "k1", "secret": "s1"}]
    assert len(fake.instances) == 1
    assert fake.instances[0].access_token == "fake-access-token"


def test_authenticate_totp_flow_calls_sdk_with_generated_code(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    import src.broker.groww as groww_module

    monkeypatch.setattr(groww_module, "pyotp", FakePyotp)
    monkeypatch.setenv("GROWW_AUTH_MODE", "totp")
    monkeypatch.setenv("GROWW_TOTP_TOKEN", "totp-token")
    monkeypatch.setenv("GROWW_TOTP_SECRET", "totp-secret")

    adapter = GrowwAdapter()
    adapter.authenticate()

    assert adapter.is_authenticated is True
    assert fake.access_token_calls == [
        {"api_key": "totp-token", "totp": "totp-for-totp-secret"}
    ]


def test_authenticate_missing_env_var_raises_authentication_error(monkeypatch):
    install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)

    adapter = GrowwAdapter()
    from src.broker.base import AuthenticationError

    with pytest.raises(AuthenticationError):
        adapter.authenticate()
    assert adapter.is_authenticated is False


def test_authenticate_translates_sdk_auth_exception(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    fake.get_access_token_raises = GrowwAPIAuthenticationException("bad creds")
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    adapter = GrowwAdapter()
    from src.broker.base import AuthenticationError

    with pytest.raises(AuthenticationError):
        adapter.authenticate()
    assert adapter.is_authenticated is False


def test_calling_api_before_authenticate_raises():
    adapter = GrowwAdapter()
    from src.broker.base import AuthenticationError

    with pytest.raises(AuthenticationError):
        adapter.get_quote(_cash_instrument("RELIANCE"))


# -- get_quote -------------------------------------------------------------


def test_get_quote_maps_response_to_quote_dataclass(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")
    fake.get_quote_response = {
        "last_price": 1419.1,
        "ohlc": {"open": 1400.0, "high": 1425.0, "low": 1395.0, "close": 1410.0},
        "volume": 12345,
        "day_change": 9.1,
        "day_change_perc": 0.64,
        "last_trade_time": 1746174479582,
        "depth": {
            "buy": [{"price": 1418.7, "quantity": 23.0}],
            "sell": [{"price": 1419.0, "quantity": 472.0}],
        },
    }

    adapter = GrowwAdapter()
    adapter.authenticate()
    instrument = _cash_instrument("RELIANCE")
    quote = adapter.get_quote(instrument)

    assert quote.last_price == 1419.1
    assert quote.open == 1400.0
    assert quote.volume == 12345
    assert quote.depth.buy[0].price == 1418.7
    assert quote.depth.sell[0].quantity == 472.0
    assert quote.as_of is not None

    call_name, call_kwargs = fake.instances[0].calls[0]
    assert call_name == "get_quote"
    assert call_kwargs["exchange"] == "NSE"
    assert call_kwargs["segment"] == "CASH"
    assert call_kwargs["trading_symbol"] == "RELIANCE"


# -- get_ltp: chunking -------------------------------------------------------


def test_get_ltp_chunks_requests_over_50_instruments(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    instruments = [_cash_instrument(f"SYM{i}") for i in range(75)]
    first_chunk_resp = {f"NSE_SYM{i}": float(i) for i in range(50)}
    second_chunk_resp = {f"NSE_SYM{i}": float(i) for i in range(50, 75)}
    fake.get_ltp_response_by_call = [first_chunk_resp, second_chunk_resp]

    adapter = GrowwAdapter()
    adapter.authenticate()
    result = adapter.get_ltp(instruments)

    assert len(result) == 75
    assert result["SYM0"] == 0.0
    assert result["SYM74"] == 74.0

    ltp_calls = [kwargs for name, kwargs in fake.instances[0].calls if name == "get_ltp"]
    assert len(ltp_calls) == 2
    assert len(ltp_calls[0]["exchange_trading_symbols"]) == 50
    assert len(ltp_calls[1]["exchange_trading_symbols"]) == 25


# -- get_historical_candles: window splitting + dedupe ----------------------


def test_get_historical_candles_splits_requests_exceeding_max_window(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    # 1-min candles: max 30-day window. Request 65 days -> 3 windows (30/30/5).
    request = HistoricalCandleRequest(
        instrument=_cash_instrument("WIPRO"),
        start_time=datetime(2025, 1, 1),
        end_time=datetime(2025, 3, 7),  # 65 days later
        interval_minutes=1,
    )
    fake.historical_candles_responses = [
        {"candles": [["2025-01-01T09:15:00", 1, 2, 0.5, 1.5, 100, None]]},
        {"candles": [["2025-01-31T09:15:00", 2, 3, 1.5, 2.5, 200, None]]},
        {"candles": [["2025-03-02T09:15:00", 3, 4, 2.5, 3.5, 300, None]]},
    ]

    adapter = GrowwAdapter()
    adapter.authenticate()
    candles = adapter.get_historical_candles(request)

    assert len(candles) == 3
    assert [c.volume for c in candles] == [100, 200, 300]
    assert candles[0].timestamp < candles[1].timestamp < candles[2].timestamp

    calls = [kwargs for name, kwargs in fake.instances[0].calls if name == "get_historical_candles"]
    assert len(calls) == 3
    assert calls[0]["candle_interval"] == "1minute"
    assert calls[0]["groww_symbol"] == "NSE-WIPRO"


def test_get_historical_candles_rejects_unsupported_interval(monkeypatch):
    install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    adapter = GrowwAdapter()
    adapter.authenticate()
    request = HistoricalCandleRequest(
        instrument=_cash_instrument("WIPRO"),
        start_time=datetime(2025, 1, 1),
        end_time=datetime(2025, 1, 2),
        interval_minutes=7,  # not a real Groww candle interval
    )
    with pytest.raises(ValueError):
        adapter.get_historical_candles(request)


# -- Rate limit retry ---------------------------------------------------


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    fake.get_quote_raises_then_succeeds = [
        GrowwAPIRateLimitException("slow down"),
        None,
    ]
    fake.get_quote_response = {
        "last_price": 100.0,
        "ohlc": {"open": 99, "high": 101, "low": 98, "close": 100},
        "volume": 1,
        "day_change": 0,
        "day_change_perc": 0,
    }

    # Avoid real sleeping during the test by patching the retry module's
    # time.sleep indirectly via monkeypatching src.utils.retry.time.sleep.
    import src.utils.retry as retry_module

    monkeypatch.setattr(retry_module.time, "sleep", lambda _seconds: None)

    adapter = GrowwAdapter()
    adapter.authenticate()
    quote = adapter.get_quote(_cash_instrument("RELIANCE"))

    assert quote.last_price == 100.0
    get_quote_calls = [n for n, _ in fake.instances[0].calls if n == "get_quote"]
    assert len(get_quote_calls) == 2  # first failed, second succeeded


def test_rate_limit_exhausts_retries_and_raises(monkeypatch):
    fake = install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    fake.get_quote_raises_then_succeeds = [
        GrowwAPIRateLimitException("slow down"),
        GrowwAPIRateLimitException("slow down"),
        GrowwAPIRateLimitException("slow down"),
        GrowwAPIRateLimitException("slow down"),
    ]

    import src.utils.retry as retry_module

    monkeypatch.setattr(retry_module.time, "sleep", lambda _seconds: None)

    adapter = GrowwAdapter()
    adapter.authenticate()

    from src.broker.base import RateLimitError

    with pytest.raises(RateLimitError):
        adapter.get_quote(_cash_instrument("RELIANCE"))


# -- Segment/scope enforcement --------------------------------------------


def test_resolve_instrument_rejects_fno_segment(monkeypatch):
    install_fake_groww(monkeypatch)
    monkeypatch.setenv("GROWW_AUTH_MODE", "api_key")
    monkeypatch.setenv("GROWW_API_KEY", "k1")
    monkeypatch.setenv("GROWW_API_SECRET", "s1")

    adapter = GrowwAdapter()
    with pytest.raises(ValueError):
        adapter.resolve_instrument("NIFTY", "NSE", "FNO")
