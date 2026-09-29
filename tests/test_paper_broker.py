from datetime import datetime, time

import pytest

from src.data.models import Tick
from src.paper.broker import BacktestBroker, BrokerConfig, PaperBroker
from src.paper.costs import CostModel
from src.paper.orders import Bar, Bracket, OrderStatus, OrderType, Side
from src.utils.config import load_yaml

COSTS = CostModel.from_dict(load_yaml("costs.yaml"))
FREE = CostModel.from_dict(load_yaml("costs.yaml") | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
CFG = BrokerConfig(starting_capital=100_000, max_position_value=25_000, max_open_positions=2,
                   slippage_bps=0, square_off=time(15, 15))
DAY = datetime(2026, 9, 25)
T = lambda h, m: DAY.replace(hour=h, minute=m)  # noqa: E731


def bar(h, m, o, hi, lo, c, sym="AAA"):
    return Bar(sym, T(h, m), o, hi, lo, c)


def broker(costs=FREE, cfg=CFG):
    b = BacktestBroker(cfg, costs)
    b.on_tick(bar(10, 15, 99, 100, 98, 99.5))        # a price is known before orders
    return b


def entered(b, bracket=Bracket(5, 7.5)):
    o = b.place_order("AAA", Side.BUY, 10, bracket=bracket, tag="ENTRY")
    [f] = b.on_tick(bar(10, 16, 100, 101, 99.5, 100.5))
    return o, f


# -- timing: no look-ahead ----------------------------------------------------------

def test_market_order_fills_at_the_next_event_open_not_the_signal_bar():
    b = broker()
    o = b.place_order("AAA", Side.BUY, 10)
    assert o.status is OrderStatus.OPEN and b.positions() == {}
    [f] = b.on_tick(bar(10, 16, 100, 101, 99.5, 100.5))
    assert (f.price, f.at, f.side) == (100, T(10, 16), Side.BUY)
    assert b.positions()["AAA"].quantity == 10


def test_order_placed_inside_a_fill_callback_waits_for_the_next_event():
    b = broker()
    placed = []
    b.add_fill_listener(lambda f: placed.append(b.place_order("AAA", Side.SELL, 10))
                        if f.side is Side.BUY else None)
    b.place_order("AAA", Side.BUY, 10)
    fills = b.on_tick(bar(10, 16, 100, 101, 99.5, 100.5))
    assert len(fills) == 1 and placed[0].status is OrderStatus.OPEN
    [exit_] = b.on_tick(bar(10, 17, 101, 102, 100, 101))
    assert exit_.price == 101


def test_limit_buy_waits_until_price_trades_through():
    b = broker()
    o = b.place_order("AAA", Side.BUY, 10, OrderType.LIMIT, limit_price=98)
    assert b.on_tick(bar(10, 16, 99, 100, 98, 99)) == []            # touch only
    [f] = b.on_tick(bar(10, 17, 99, 99, 97.5, 98))
    assert f.price == 98 and o.status is OrderStatus.FILLED


# -- brackets -----------------------------------------------------------------------

def test_bracket_target_fills_and_cancels_the_stop():
    b = broker()
    _, f = entered(b)
    stop, target = b.open_orders("AAA")
    assert (stop.type, stop.trigger_price, target.type, target.limit_price) == \
        (OrderType.STOP, 95.0, OrderType.LIMIT, 107.5)
    [x] = b.on_tick(bar(10, 17, 101, 108, 100.8, 107))
    assert (x.tag, x.price) == ("TARGET", 107.5)
    assert stop.status is OrderStatus.CANCELLED and b.open_orders() == []
    [t] = b.trades
    assert t["gross_pnl"] == pytest.approx(75.0) and t["exit_tag"] == "TARGET"


def test_stop_and_target_in_one_bar_counts_as_stop():
    b = broker()
    entered(b)
    [x] = b.on_tick(bar(10, 17, 100, 110, 90, 105))
    assert (x.tag, x.price) == ("STOP_LOSS", 95.0)
    assert x.note == "stop and target touched in one bar: stop assumed first"


def test_gap_through_stop_fills_at_the_worse_open():
    b = broker()
    entered(b)
    [x] = b.on_tick(bar(10, 17, 92, 93, 91, 92.5))
    assert (x.tag, x.price) == ("STOP_LOSS", 92.0)


def test_bracket_exits_are_checked_on_the_entry_bar_after_the_open():
    b = broker()
    b.place_order("AAA", Side.BUY, 10, bracket=Bracket(5, 7.5))
    entry, stop = b.on_tick(bar(10, 16, 100, 100.5, 94, 95.5))
    assert (entry.price, stop.tag, stop.price) == (100, "STOP_LOSS", 95.0)


def test_pct_bracket_is_relative_to_the_fill():
    b = broker()
    entered(b, Bracket(0.6, 1.5, pct=True))
    stop, target = b.open_orders("AAA")
    assert (stop.trigger_price, target.limit_price) == (99.4, 101.5)


# -- money ----------------------------------------------------------------------------

def test_cash_and_pnl_include_every_charge():
    b = broker(COSTS)
    _, buy = entered(b, bracket=None)
    assert b.cash == pytest.approx(100_000 - 1000 - buy.charges.total)
    b.place_order("AAA", Side.SELL, 10)
    [sell] = b.on_tick(bar(10, 17, 102, 103, 101, 102))
    [t] = b.trades
    assert t["gross_pnl"] == pytest.approx(20.0)
    assert t["charges"] == pytest.approx(buy.charges.total + sell.charges.total)
    assert t["net_pnl"] == pytest.approx(20.0 - t["charges"])
    assert b.equity() == pytest.approx(100_000 + t["net_pnl"])
    assert sell.charges.stt > 0 and buy.charges.stamp_duty > 0


def test_slippage_goes_against_us_both_ways():
    b = broker(cfg=BrokerConfig(100_000, 25_000, 2, slippage_bps=10, square_off=time(15, 15)))
    b.place_order("AAA", Side.BUY, 10)
    [f] = b.on_tick(bar(10, 16, 100, 101, 99.5, 100.5))
    b.place_order("AAA", Side.SELL, 10)
    [g] = b.on_tick(bar(10, 17, 100, 101, 99.5, 100.5))
    assert (f.price, g.price) == (pytest.approx(100.1), pytest.approx(99.9))


# -- limits ---------------------------------------------------------------------------

def test_position_value_limit_rejects():
    o = broker().place_order("AAA", Side.BUY, 300)                    # ~Rs 29,850 > 25,000
    assert o.status is OrderStatus.REJECTED and "position value" in o.reason


def test_max_open_positions_rejects_a_new_symbol():
    b = broker()
    for s in ("BBB", "CCC"):
        b.on_tick(bar(10, 15, 50, 50, 50, 50, sym=s))
    assert b.place_order("AAA", Side.BUY, 1).status is OrderStatus.OPEN
    assert b.place_order("BBB", Side.BUY, 1).status is OrderStatus.OPEN
    o = b.place_order("CCC", Side.BUY, 1)
    assert o.status is OrderStatus.REJECTED and "max open positions" in o.reason


def test_insufficient_capital_rejects():
    cfg = BrokerConfig(1_000, 25_000, 2, 0, time(15, 15))
    o = broker(cfg=cfg).place_order("AAA", Side.BUY, 20)
    assert o.status is OrderStatus.REJECTED and "capital" in o.reason


def test_long_only_sell_beyond_position_is_rejected():
    o = broker().place_order("AAA", Side.SELL, 5)
    assert o.status is OrderStatus.REJECTED and "long-only" in o.reason


def test_no_price_yet_rejects_market_order():
    o = broker().place_order("ZZZ", Side.BUY, 1)
    assert o.status is OrderStatus.REJECTED and "no price" in o.reason


def test_bad_quantity_rejected():
    assert broker().place_order("AAA", Side.BUY, 0).status is OrderStatus.REJECTED


# -- intraday rules ---------------------------------------------------------------------

def test_square_off_closes_positions_cancels_orders_and_blocks_entries():
    b = broker()
    entered(b)
    b.on_tick(bar(15, 14, 101, 101.5, 100.5, 101.2))
    fills = b.square_off(T(15, 15))
    assert [(f.tag, f.price, f.at) for f in fills] == [("SQUARE_OFF", 101.2, T(15, 15))]
    assert b.open_orders() == [] and b.positions() == {}
    o = b.place_order("AAA", Side.BUY, 1)
    assert o.status is OrderStatus.REJECTED and "square-off" in o.reason


def test_new_day_reopens_entries():
    b = broker()
    b.square_off(T(15, 15))
    b.on_tick(Bar("AAA", datetime(2026, 9, 26, 9, 15), 100, 100, 100, 100))
    assert b.place_order("AAA", Side.BUY, 1).status is OrderStatus.OPEN


def test_cancel_order():
    b = broker()
    o = b.place_order("AAA", Side.BUY, 10, OrderType.LIMIT, limit_price=90)
    assert b.cancel_order(o.id) and o.status is OrderStatus.CANCELLED
    assert not b.cancel_order(o.id) and not b.cancel_order("nope")


# -- paper broker (live ticks) ------------------------------------------------------------

def test_paper_broker_fills_on_the_next_live_tick():
    b = PaperBroker(CFG, FREE)
    b.on_tick(Tick("AAA", T(10, 15), 100.0))
    b.place_order("AAA", Side.BUY, 10)
    [f] = b.on_tick(Tick("AAA", T(10, 15).replace(second=2), 100.4))
    assert f.price == 100.4 and b.positions()["AAA"].quantity == 10
