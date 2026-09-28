import threading
from datetime import datetime, timedelta

import pytest

from src.data.feed_store import FeedStore
from src.data.models import DepthSnapshot, Tick

T = datetime(2026, 9, 28, 11, 0, 0)


def depth(ts, bid=100.0, ask=100.1):
    return DepthSnapshot("AAA", ts, bid, 10, ask, 20, 500, 300)


def test_empty_snapshot():
    snap = FeedStore().snapshot(T)
    assert snap.symbols == {} and snap.last_any_tick_age_s is None and snap.bad_payloads == 0


def test_ltp_dedupe_and_out_of_order_rejected():
    s = FeedStore()
    assert s.put_ltp(Tick("AAA", T, 100.0), T) is True
    assert s.put_ltp(Tick("AAA", T, 100.0), T) is False                          # duplicate
    assert s.put_ltp(Tick("AAA", T - timedelta(seconds=1), 99.0), T) is False    # older
    assert s.put_ltp(Tick("AAA", T + timedelta(seconds=1), 100.5), T) is True
    assert s.snapshot(T).symbols["AAA"].ltp == 100.5


def test_depth_dedupe_and_age_uses_arrival_time():
    s = FeedStore()
    exchange_ts = T - timedelta(hours=1)                  # skewed exchange clock is ignored
    assert s.put_depth(depth(exchange_ts), T) is True
    assert s.put_depth(depth(exchange_ts), T) is False
    st = s.snapshot(T + timedelta(seconds=7)).symbols["AAA"]
    assert st.depth.best_bid == 100.0 and st.depth_age_s == 7


def test_tick_counts_and_rolling_window():
    s = FeedStore()
    for i in range(300):                                   # one tick per second for 5 min
        s.count_tick("AAA", T + timedelta(seconds=i))
    now = T + timedelta(seconds=300)
    st = s.snapshot(now).symbols["AAA"]
    assert st.ticks_1m == 60 and st.ticks_5m_avg == pytest.approx(60.0)
    assert st.last_tick_age_s == 1
    later = s.snapshot(now + timedelta(minutes=10)).symbols["AAA"]
    assert later.ticks_1m == 0 and later.ticks_5m_avg == 0


def test_last_any_tick_age_and_bad_payloads():
    s = FeedStore()
    s.count_tick("AAA", T)
    s.count_tick("NIFTY", T + timedelta(seconds=4))
    s.note_bad_payload()
    snap = s.snapshot(T + timedelta(seconds=10))
    assert snap.last_any_tick_age_s == 6 and snap.bad_payloads == 1


def test_snapshot_is_immutable():
    s = FeedStore()
    s.count_tick("AAA", T)
    snap = s.snapshot(T)
    with pytest.raises(TypeError):
        snap.symbols["BBB"] = None
    s.count_tick("BBB", T)
    assert "BBB" not in snap.symbols


def test_concurrent_writers():
    s = FeedStore()

    def writer(sym):
        for i in range(1000):
            s.count_tick(sym, T + timedelta(milliseconds=i))
            s.put_ltp(Tick(sym, T + timedelta(milliseconds=i), 100.0 + i), T)

    threads = [threading.Thread(target=writer, args=(f"S{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = s.snapshot(T + timedelta(seconds=2))
    assert all(snap.symbols[f"S{n}"].ticks_1m == 1000 for n in range(4))
    assert all(snap.symbols[f"S{n}"].ltp == 1099.0 for n in range(4))


def test_out_of_order_arrivals_keep_newest_as_last_tick():
    s = FeedStore()
    s.count_tick("AAA", T)
    s.count_tick("AAA", T - timedelta(seconds=30))
    assert s.snapshot(T + timedelta(seconds=2)).symbols["AAA"].last_tick_age_s == 2
