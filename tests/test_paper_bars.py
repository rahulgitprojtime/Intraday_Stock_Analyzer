from datetime import datetime, timedelta

from src.paper.bars import BarAggregator, bullish_reversal
from src.paper.orders import Bar

T0 = datetime(2026, 9, 25, 9, 15)


def one_min(i, o=100, h=101, l=99, c=100, v=10, sym="AAA", start=T0):
    return Bar(sym, start + timedelta(minutes=i), o, h, l, c, v)


def test_five_minute_bars_are_session_aligned_and_close_on_their_last_minute():
    agg = BarAggregator(5)
    closed = []
    for i in range(10):
        b = one_min(i, o=100 + i, h=101 + i, l=99 + i, c=100.5 + i, v=i + 1)
        agg.add(b)
        c = agg.flush(b.ts + timedelta(minutes=1))
        if c:
            closed.append(c)
    first, second = closed
    assert (first.ts, first.open, first.high, first.low, first.close, first.volume) == \
        (T0, 100, 105, 99, 104.5, 15)
    assert second.ts == T0 + timedelta(minutes=5) and agg.done == closed


def test_missing_last_minute_still_closes_the_bucket_on_time():
    agg = BarAggregator(5)
    for i in range(4):                                   # 09:19 bar missing (data gap)
        agg.add(one_min(i))
    assert agg.flush(T0 + timedelta(minutes=4)) is None  # 09:19: bucket not over yet
    assert agg.flush(T0 + timedelta(minutes=5)).ts == T0


def test_a_later_bar_closes_a_stale_bucket():
    agg = BarAggregator(5)
    agg.add(one_min(0))
    assert agg.add(one_min(7)).ts == T0                  # gap: 09:22 closes the 09:15 bucket


def test_seed_prior_day_bars_for_warmup():
    agg = BarAggregator(15)
    prior = [one_min(i, start=T0 - timedelta(days=1)) for i in range(375)]
    agg.seed(prior)
    assert len(agg.done) == 25 and agg.done[0].ts == T0 - timedelta(days=1)
    assert agg.today(T0.date()) == []


def bar(o, h, l, c):
    return Bar("AAA", T0, o, h, l, c)


def test_bullish_engulfing():
    assert bullish_reversal(bar(101, 101.5, 99.8, 100), bar(99.9, 101.6, 99.7, 101.2)) == \
        "ENGULFING"


def test_hammer_and_pin_bar():
    assert bullish_reversal(None, bar(100, 100.3, 97, 100.2)) == "HAMMER"   # long lower wick
    assert bullish_reversal(None, bar(100, 100.9, 97, 99.8)) == "PIN_BAR"  # wick, weak body


def test_not_a_reversal():
    assert bullish_reversal(None, bar(100, 102, 99.5, 101.8)) is None      # plain green bar
    assert bullish_reversal(None, bar(100, 100, 100, 100)) is None         # no range
