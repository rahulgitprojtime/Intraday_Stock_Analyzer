"""Stand-in for `growwapi.GrowwFeed` (growwapi 1.5.0 source, read 2026-09-28).

Mirrors the verified surface: constructed with a GrowwAPI client;
`subscribe_*` take `[{"exchange", "segment", "exchange_token"}]` and an
optional `on_data_received(meta)` callback that gets only meta; getters
return `{exchange: {segment: {feed_key: data}}}` for subscribed topics.
"""

from __future__ import annotations


class FakeGrowwFeed:
    instances: list = []
    fail_init: int = 0          # raise on the next N constructions

    def __init__(self, client) -> None:
        if FakeGrowwFeed.fail_init > 0:
            FakeGrowwFeed.fail_init -= 1
            raise ConnectionError("socket token failed")
        self.client = client
        self.subs: dict = {}
        self.callbacks: dict = {}
        self.unsubscribed: list = []
        self.ltp: dict = {}
        self.depth: dict = {}
        self.index: dict = {}
        self.raise_on_get: set = set()
        FakeGrowwFeed.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances, cls.fail_init = [], 0

    def _sub(self, kind, instruments, cb):
        if not instruments:
            raise ValueError("At least one instrument must be provided")
        self.subs[kind] = list(instruments)
        self.callbacks[kind] = cb

    def subscribe_ltp(self, instrument_list, on_data_received=None):
        self._sub("ltp", instrument_list, on_data_received)

    def subscribe_market_depth(self, instrument_list, on_data_received=None):
        self._sub("market_depth", instrument_list, on_data_received)

    def subscribe_index_value(self, instrument_list, on_data_received=None):
        self._sub("index_value", instrument_list, on_data_received)

    def unsubscribe_ltp(self, instrument_list):
        self.unsubscribed.append("ltp")

    def unsubscribe_market_depth(self, instrument_list):
        self.unsubscribed.append("market_depth")

    def unsubscribe_index_value(self, instrument_list):
        self.unsubscribed.append("index_value")

    def _get(self, kind, data):
        if kind in self.raise_on_get:
            raise RuntimeError(f"{kind} parse failed")
        return {"NSE": {"CASH": dict(data)}} if data else {}

    def get_ltp(self):
        return self._get("ltp", self.ltp)

    def get_market_depth(self):
        return self._get("market_depth", self.depth)

    def get_index_value(self):
        return self._get("index_value", self.index)

    def emit(self, kind: str, feed_key: str) -> None:
        """Simulate the SDK calling `on_data_received(meta)`."""
        cb = self.callbacks.get(kind)
        if cb:
            cb({"exchange": "NSE", "segment": "CASH", "feed_key": feed_key, "feed_type": kind})
