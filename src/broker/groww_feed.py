"""Groww live feed wrapper — M7 (spec 2026-09-28 §2-3, §6).

The only feed code that touches `growwapi`. Read-only market data: LTP,
market depth and index value; order/position feeds are never subscribed
(DECISIONS #8).

Verified from the growwapi 1.5.0 source: `on_data_received` gets only
`meta` (exchange, segment, feed_key, feed_type), never the data, and the
getters parse every subscribed topic per call. So the callback only counts
ticks (cheap, exact velocity) and a poller thread reads latest values once
per `poll_seconds`. Nothing here ever raises into the SDK's thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import datetime

from src.data.feed_store import FeedStore
from src.data.models import DepthSnapshot, Instrument, Tick

try:
    from growwapi import GrowwFeed
except ImportError:  # SDK optional in dev/CI; `feed_cls` is injected in tests
    GrowwFeed = None

TRADE_FEEDS = ("ltp", "index_value")      # depth updates are not trades


def _ts(ms) -> datetime:
    """Exchange epoch millis → naive local time (the worker assumes IST)."""
    return datetime.fromtimestamp(float(ms) / 1000)


def _items(payload) -> list[tuple[str, dict | None]]:
    """Flatten `{exchange: {segment: {feed_key: data}}}`."""
    out = []
    for segments in (payload or {}).values():
        for items in (segments or {}).values():
            out += list((items or {}).items())
    return out


def _side(book: dict, best: Callable) -> tuple[float, float, float]:
    """(best price, qty at best, total qty) over filled levels; empty
    levels (price or qty 0) are skipped; a side with none is invalid."""
    levels = [(float(v["price"]), float(v["qty"])) for v in (book or {}).values()]
    levels = [(p, q) for p, q in levels if p > 0 and q > 0]
    if not levels:
        raise ValueError("no filled levels on a book side")
    price, qty = best(levels, key=lambda pq: pq[0])
    return price, qty, sum(q for _, q in levels)


def parse_depth(symbol: str, data: dict) -> DepthSnapshot:
    bid, bid_qty, total_bid = _side(data["buyBook"], max)
    ask, ask_qty, total_ask = _side(data["sellBook"], min)
    return DepthSnapshot(symbol, _ts(data["tsInMillis"]), bid, bid_qty, ask, ask_qty,
                         total_bid, total_ask)


class LiveFeed:
    def __init__(self, client_factory: Callable[[], object], stocks: Sequence[Instrument],
                 index: Instrument | None, store: FeedStore, *, feed_cls=None,
                 poll_seconds: float = 1.0, clock: Callable[[], datetime] = datetime.now):
        self._client_factory = client_factory
        self._stocks = [s for s in stocks if s.exchange_token]
        self._index = index
        self._store = store
        self._feed_cls = feed_cls or GrowwFeed
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._by_key = {s.exchange_token: s.trading_symbol for s in self._stocks}
        if index is not None:
            self._by_key[self._index_key()] = index.trading_symbol
        self._feed = None
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._feed is not None

    @property
    def subscribed(self) -> int:
        return 2 * len(self._stocks) + (1 if self._index is not None else 0)

    def _index_key(self) -> str:
        # Verified: the index feed key is the symbol (e.g. NIFTY)
        return self._index.exchange_token or self._index.trading_symbol

    def _subs(self, client, keys: list[str]) -> list[dict]:
        return [{"exchange": client.EXCHANGE_NSE, "segment": client.SEGMENT_CASH,
                 "exchange_token": k} for k in keys]

    def start(self) -> None:
        if self._feed_cls is None:
            raise RuntimeError("growwapi is not installed")
        client = self._client_factory()
        feed = self._feed_cls(client)          # mints a fresh socket token
        stock_subs = self._subs(client, [s.exchange_token for s in self._stocks])
        if stock_subs:
            feed.subscribe_ltp(stock_subs, on_data_received=self._on_data)
            feed.subscribe_market_depth(stock_subs)
        if self._index is not None:
            feed.subscribe_index_value(self._subs(client, [self._index_key()]),
                                       on_data_received=self._on_data)
        self._feed, self._client = feed, client
        self._stop.clear()
        self._poller = threading.Thread(target=self._poll_loop, name="feed-poller", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        if self._poller is not None and self._poller is not threading.current_thread():
            self._poller.join(timeout=5)
        self._poller = None
        feed, self._feed = self._feed, None
        if feed is None:
            return
        stock_subs = self._subs(self._client, [s.exchange_token for s in self._stocks])
        calls = [(feed.unsubscribe_ltp, stock_subs), (feed.unsubscribe_market_depth, stock_subs)]
        if self._index is not None:
            calls.append((feed.unsubscribe_index_value,
                          self._subs(self._client, [self._index_key()])))
        for fn, subs in calls:
            if subs:
                try:
                    fn(subs)
                except Exception as exc:       # best effort; the SDK thread is a daemon
                    self.last_error = f"unsubscribe: {exc}"

    def restart(self) -> None:
        self.stop()
        self.start()

    # -- SDK thread ----------------------------------------------------------

    def _on_data(self, meta) -> None:
        try:
            if meta.get("feed_type") in TRADE_FEEDS:
                sym = self._by_key.get(str(meta.get("feed_key")))
                if sym is not None:
                    self._store.count_tick(sym, self._clock())
        except Exception:                      # never raise into the SDK event loop
            self._store.note_bad_payload()

    # -- poller thread -------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            self.poll_once()

    def poll_once(self) -> None:
        feed = self._feed
        if feed is None:
            return
        now = self._clock()
        for name, getter, parse in (
            ("ltp", feed.get_ltp, lambda s, d: self._store.put_ltp(
                Tick(s, _ts(d["tsInMillis"]), float(d["ltp"])), now)),
            ("market_depth", feed.get_market_depth, lambda s, d: self._store.put_depth(
                parse_depth(s, d), now)),
            ("index_value", feed.get_index_value, lambda s, d: self._store.put_ltp(
                Tick(s, _ts(d["tsInMillis"]), float(d["value"])), now)),
        ):
            try:
                payload = getter()
            except Exception as exc:           # e.g. not subscribed / no data yet
                self.last_error = f"{name}: {exc}"
                continue
            for key, data in _items(payload):
                sym = self._by_key.get(str(key))
                if sym is None or data is None:     # null = no message yet (verified live)
                    continue
                try:
                    parse(sym, data)
                except Exception:
                    self._store.note_bad_payload()
