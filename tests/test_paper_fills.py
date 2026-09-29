from datetime import datetime

import pytest

from src.paper.fills import fill_price
from src.paper.orders import Bar, Order, OrderType, Side

T = datetime(2026, 9, 25, 10, 16)


def order(side="BUY", type_="MARKET", limit=None, trigger=None):
    return Order("o1", "AAA", Side(side), 10, OrderType(type_), T, limit_price=limit,
                 trigger_price=trigger)


def bar(o, h, l, c):
    return Bar("AAA", T, o, h, l, c)


# -- market -------------------------------------------------------------------

def test_market_buy_fills_at_open_plus_slippage():
    assert fill_price(order(), bar(100, 101, 99, 100.5), slippage_bps=5) == pytest.approx(100.05)


def test_market_sell_fills_at_open_minus_slippage():
    assert fill_price(order("SELL"), bar(100, 101, 99, 100.5), 5) == pytest.approx(99.95)


def test_market_fills_on_a_tick_at_its_price():
    assert fill_price(order(), Bar.tick("AAA", T, 250.0), 0) == 250.0


# -- limit: only when price trades THROUGH the limit ---------------------------

def test_buy_limit_touch_is_not_a_fill():
    assert fill_price(order(type_="LIMIT", limit=99), bar(100, 101, 99, 100), 5) is None


def test_buy_limit_fills_at_limit_when_traded_through_no_slippage():
    assert fill_price(order(type_="LIMIT", limit=99), bar(100, 101, 98.9, 100), 5) == 99


def test_buy_limit_gap_below_fills_at_better_open():
    assert fill_price(order(type_="LIMIT", limit=99), bar(97, 98, 96, 97.5), 5) == 97


def test_sell_limit_touch_is_not_a_fill_through_is():
    o = order("SELL", "LIMIT", limit=105)
    assert fill_price(o, bar(100, 105, 99, 104), 5) is None
    assert fill_price(o, bar(100, 105.1, 99, 104), 5) == 105
    assert fill_price(o, bar(106, 107, 105.5, 106), 5) == 106        # gap up: better open


def test_limit_on_ticks_needs_a_print_through_the_limit():
    o = order(type_="LIMIT", limit=99)
    assert fill_price(o, Bar.tick("AAA", T, 99.0), 0) is None
    assert fill_price(o, Bar.tick("AAA", T, 98.95), 0) == 98.95


# -- stop (stop-market) -----------------------------------------------------------

def test_sell_stop_triggers_on_touch_fills_at_trigger_minus_slippage():
    o = order("SELL", "STOP", trigger=95)
    assert fill_price(o, bar(99, 99.5, 95.01, 96), 0) is None
    assert fill_price(o, bar(99, 99.5, 95, 96), 0) == 95
    assert fill_price(o, bar(99, 99.5, 94, 96), 10) == pytest.approx(95 * 0.999)


def test_sell_stop_gap_through_fills_at_worse_open():
    assert fill_price(order("SELL", "STOP", trigger=95), bar(92, 93, 91, 92.5), 0) == 92


def test_buy_stop_triggers_above():
    o = order("BUY", "STOP", trigger=110)
    assert fill_price(o, bar(108, 109.9, 107, 109), 0) is None
    assert fill_price(o, bar(108, 111, 107, 110.5), 0) == 110
    assert fill_price(o, bar(112, 113, 111, 112), 0) == 112          # gap up: worse open


def test_missing_prices_raise():
    with pytest.raises(ValueError):
        fill_price(order(type_="LIMIT"), bar(100, 101, 99, 100), 0)
