import time
from datetime import datetime

import pytest

from src.broker.groww_feed import LiveFeed
from src.data.feed_store import FeedStore
from src.data.models import Exchange, Instrument, Segment
from tests.fakes.fake_groww_feed import FakeGrowwFeed

T = datetime(2026, 9, 28, 11, 0, 0)
MS = int(T.timestamp() * 1000)


class Client:
    EXCHANGE_NSE, SEGMENT_CASH = "NSE", "CASH"


REL = Instrument("RELIANCE", Exchange.NSE, Segment.CASH, exchange_token="2885")
TCS = Instrument("TCS", Exchange.NSE, Segment.CASH, exchange_token="11536")
NIFTY = Instrument("NIFTY", Exchange.NSE, Segment.CASH, exchange_token=None, is_index=True)


@pytest.fixture
def feed():
    FakeGrowwFeed.reset()
    store = FeedStore()
    lf = LiveFeed(lambda: Client, [REL, TCS], NIFTY, store, feed_cls=FakeGrowwFeed,
                  clock=lambda: T, poll_seconds=3600)
    yield lf, store
    lf.stop()


def book(bid=1206.3, ask=1206.4, bq=(100, 50), aq=(80, 20)):
    return {"tsInMillis": MS,
            "buyBook": {"1": {"price": bid, "qty": bq[0]}, "2": {"price": bid - 0.1, "qty": bq[1]}},
            "sellBook": {"1": {"price": ask, "qty": aq[0]}, "2": {"price": ask + 0.1, "qty": aq[1]}}}


def test_start_subscribes_by_exchange_token_and_index_symbol(feed):
    lf, _ = feed
    lf.start()
    sdk = FakeGrowwFeed.instances[-1]
    assert [i["exchange_token"] for i in sdk.subs["ltp"]] == ["2885", "11536"]
    assert sdk.subs["market_depth"] == sdk.subs["ltp"]
    assert sdk.subs["index_value"] == [{"exchange": "NSE", "segment": "CASH",
                                        "exchange_token": "NIFTY"}]
    assert lf.subscribed == 5


def test_callback_counts_ticks_by_symbol(feed):
    lf, store = feed
    lf.start()
    sdk = FakeGrowwFeed.instances[-1]
    for _ in range(3):
        sdk.emit("ltp", "2885")
    sdk.emit("index_value", "NIFTY")
    sdk.emit("market_depth", "2885")          # depth updates are not trades
    sdk.emit("ltp", "999999")                 # unknown token ignored
    snap = store.snapshot(T)
    assert snap.symbols["RELIANCE"].ticks_1m == 3 and snap.symbols["NIFTY"].ticks_1m == 1
    assert "999999" not in snap.symbols


def test_poll_once_parses_ltp_depth_index(feed):
    lf, store = feed
    lf.start()
    sdk = FakeGrowwFeed.instances[-1]
    sdk.ltp = {"2885": {"tsInMillis": MS, "ltp": 1206.35}}
    sdk.depth = {"2885": book()}
    sdk.index = {"NIFTY": {"tsInMillis": MS, "value": 22818.15}}
    lf.poll_once()
    snap = store.snapshot(T)
    rel = snap.symbols["RELIANCE"]
    assert rel.ltp == 1206.35 and snap.symbols["NIFTY"].ltp == 22818.15
    d = rel.depth
    assert (d.best_bid, d.best_ask, d.bid_qty, d.ask_qty) == (1206.3, 1206.4, 100, 80)
    assert (d.total_bid_qty, d.total_ask_qty) == (150, 100)
    assert d.ts == T and snap.bad_payloads == 0


@pytest.mark.parametrize("payload", [
    {"tsInMillis": MS, "buyBook": {}, "sellBook": {}},
    {"tsInMillis": MS, "buyBook": {"1": {"price": 100, "qty": 5}}, "sellBook": {}},
    {"tsInMillis": MS, "buyBook": {"1": {"price": 0, "qty": 5}},
     "sellBook": {"1": {"price": 100.1, "qty": 5}}},
    {"buyBook": {"1": {"price": 100, "qty": 5}}, "sellBook": {"1": {"price": 100.1, "qty": 5}}},
    {"tsInMillis": MS, "buyBook": {"1": {"price": "x", "qty": 5}},
     "sellBook": {"1": {"price": 100.1, "qty": 5}}},
])
def test_bad_depth_payloads_counted_not_raised(feed, payload):
    lf, store = feed
    lf.start()
    FakeGrowwFeed.instances[-1].depth = {"2885": payload}
    lf.poll_once()
    snap = store.snapshot(T)
    assert snap.bad_payloads == 1
    assert "RELIANCE" not in snap.symbols or snap.symbols["RELIANCE"].depth is None


def test_bad_ltp_and_getter_exceptions_do_not_stop_polling(feed):
    lf, store = feed
    lf.start()
    sdk = FakeGrowwFeed.instances[-1]
    sdk.ltp = {"2885": {"tsInMillis": MS, "ltp": None},
               "11536": {"tsInMillis": MS, "ltp": 4000.0}}
    sdk.raise_on_get = {"market_depth"}
    lf.poll_once()
    snap = store.snapshot(T)
    assert snap.symbols["TCS"].ltp == 4000.0 and snap.bad_payloads == 1
    assert "market_depth parse failed" in lf.last_error


def test_stop_unsubscribes_and_restart_builds_a_new_sdk_feed(feed):
    lf, _ = feed
    lf.start()
    first = FakeGrowwFeed.instances[-1]
    lf.restart()
    assert set(first.unsubscribed) == {"ltp", "market_depth", "index_value"}
    assert FakeGrowwFeed.instances[-1] is not first and lf.running
    lf.stop()
    assert not lf.running


def test_start_failure_propagates_and_leaves_feed_stopped(feed):
    lf, _ = feed
    FakeGrowwFeed.fail_init = 1
    with pytest.raises(ConnectionError):
        lf.start()
    assert not lf.running
    lf.stop()                                 # safe when never started


def test_poller_thread_runs_until_stopped():
    FakeGrowwFeed.reset()
    store = FeedStore()
    lf = LiveFeed(lambda: Client, [REL], None, store, feed_cls=FakeGrowwFeed, poll_seconds=0.01)
    lf.start()
    FakeGrowwFeed.instances[-1].ltp = {"2885": {"tsInMillis": MS, "ltp": 1.0}}
    deadline = time.time() + 2
    while time.time() < deadline and "RELIANCE" not in store.snapshot(datetime.now()).symbols:
        time.sleep(0.01)
    lf.stop()
    assert store.snapshot(datetime.now()).symbols["RELIANCE"].ltp == 1.0


def test_null_payload_is_no_data_yet_not_bad(feed):
    """Live 2026-09-28: getters return null data for a subscribed topic until
    its first message arrives."""
    lf, store = feed
    lf.start()
    sdk = FakeGrowwFeed.instances[-1]
    sdk.ltp, sdk.depth = {"2885": None}, {"2885": None}
    lf.poll_once()
    snap = store.snapshot(T)
    assert snap.bad_payloads == 0 and "RELIANCE" not in snap.symbols


def test_empty_zero_price_levels_are_skipped_not_fatal(feed):
    lf, store = feed
    lf.start()
    payload = book()
    payload["buyBook"]["3"] = {"price": 0, "qty": 0}
    payload["sellBook"]["3"] = {"price": 0, "qty": 0}
    FakeGrowwFeed.instances[-1].depth = {"2885": payload}
    lf.poll_once()
    d = store.snapshot(T).symbols["RELIANCE"].depth
    assert (d.best_bid, d.best_ask, d.total_bid_qty, d.total_ask_qty) == (1206.3, 1206.4, 150, 100)


def test_set_stocks_resubscribes_new_universe(feed):
    lf, _ = feed
    lf.start()
    first = FakeGrowwFeed.instances[-1]
    INFY = Instrument("INFY", Exchange.NSE, Segment.CASH, exchange_token="1594")
    lf.set_stocks([REL, INFY])
    sdk = FakeGrowwFeed.instances[-1]
    assert sdk is not first and [i["exchange_token"] for i in sdk.subs["ltp"]] == ["2885", "1594"]
    sdk.emit("ltp", "1594")
    assert lf._store.snapshot(T).symbols["INFY"].ticks_1m == 1
    assert lf.subscribed == 5


def test_set_stocks_before_start_does_not_connect(feed):
    lf, _ = feed
    lf.set_stocks([REL])
    assert FakeGrowwFeed.instances == [] and not lf.running
