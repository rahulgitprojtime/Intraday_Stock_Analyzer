"""Test doubles standing in for the real `growwapi` package, which isn't
installed in every dev/CI environment (and shouldn't need to be, to test
our own logic — auth mode switching, chunking, window-splitting, retry).

These fakes deliberately mirror the *shape* verified in
docs/groww_api_notes.md (constants, method signatures, response payloads)
so a test failure here is a real signal, not an artifact of a sloppy fake.
"""

from __future__ import annotations

from src.broker import groww as groww_module


class FakeGrowwAPI:
    """Stands in for growwapi.GrowwAPI. Class-level call logs let tests
    assert on what the adapter actually sent."""

    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    SEGMENT_CASH = "CASH"
    CANDLE_INTERVAL_MIN_1 = "1minute"
    CANDLE_INTERVAL_MIN_5 = "5minute"
    CANDLE_INTERVAL_MIN_15 = "15minute"
    CANDLE_INTERVAL_MIN_30 = "30minute"
    CANDLE_INTERVAL_HOUR_1 = "1hour"

    access_token_calls: list[dict] = []
    instances: list["FakeGrowwAPI"] = []

    # Configurable behavior, set per-test on the class before constructing.
    get_access_token_raises: BaseException | None = None
    get_quote_response: dict | None = None
    get_ltp_response_by_call: list[dict] | None = None   # one dict per call, in order
    get_ohlc_response_by_call: list[dict] | None = None
    historical_candles_responses: list[dict] | None = None  # one per window call
    get_quote_raises_then_succeeds: list[BaseException] | None = None

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.calls: list[tuple[str, dict]] = []
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.access_token_calls = []
        cls.instances = []
        cls.get_access_token_raises = None
        cls.get_quote_response = None
        cls.get_ltp_response_by_call = None
        cls.get_ohlc_response_by_call = None
        cls.historical_candles_responses = None
        cls.get_quote_raises_then_succeeds = None

    @classmethod
    def get_access_token(cls, **kwargs) -> str:
        cls.access_token_calls.append(kwargs)
        if cls.get_access_token_raises is not None:
            raise cls.get_access_token_raises
        return "fake-access-token"

    def get_quote(self, **kwargs) -> dict:
        self.calls.append(("get_quote", kwargs))
        if self.get_quote_raises_then_succeeds:
            exc = self.get_quote_raises_then_succeeds.pop(0)
            if exc is not None:
                raise exc
        return self.get_quote_response

    def get_ltp(self, **kwargs) -> dict:
        idx = sum(1 for name, _ in self.calls if name == "get_ltp")
        self.calls.append(("get_ltp", kwargs))
        return self.get_ltp_response_by_call[idx]

    def get_ohlc(self, **kwargs) -> dict:
        idx = sum(1 for name, _ in self.calls if name == "get_ohlc")
        self.calls.append(("get_ohlc", kwargs))
        return self.get_ohlc_response_by_call[idx]

    def get_historical_candles(self, **kwargs) -> dict:
        idx = sum(1 for name, _ in self.calls if name == "get_historical_candles")
        self.calls.append(("get_historical_candles", kwargs))
        return self.historical_candles_responses[idx]


def install_fake_groww(monkeypatch) -> type[FakeGrowwAPI]:
    """Patch src.broker.groww.GrowwAPI with a fresh FakeGrowwAPI class."""
    FakeGrowwAPI.reset()
    monkeypatch.setattr(groww_module, "GrowwAPI", FakeGrowwAPI)
    return FakeGrowwAPI


class FakeTOTP:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def now(self) -> str:
        return f"totp-for-{self.secret}"


class FakePyotp:
    TOTP = FakeTOTP
