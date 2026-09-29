"""Fill rules — DECISIONS #29. SIMULATION ONLY.

Whether and where an open order fills against one market event (a 1-min
bar, or a live tick = a bar with O = H = L = C). The broker only offers an
order events that started after it was placed, so a signal on bar T's close
can fill at bar T+1's open at the earliest.

- MARKET: the event's open, plus slippage against us.
- LIMIT: only when price trades THROUGH the limit (a touch is not a fill:
  the queue ahead of us may absorb it). Fills at the limit, or at a better
  open after a gap. No slippage: the price is guaranteed.
- STOP (stop-market): triggers when price touches the trigger, then fills
  like a market order at the trigger, or at a worse open after a gap, plus
  slippage.
"""

from __future__ import annotations

from src.paper.orders import Bar, Order, OrderType, Side


def slip(price: float, side: Side, bps: float) -> float:
    sign = 1 if side is Side.BUY else -1
    return round(price * (1 + sign * bps / 10_000), 4)


def fill_price(order: Order, bar: Bar, slippage_bps: float) -> float | None:
    buy = order.side is Side.BUY
    if order.type is OrderType.MARKET:
        return slip(bar.open, order.side, slippage_bps)
    if order.type is OrderType.LIMIT:
        lim = order.limit_price
        if lim is None:
            raise ValueError(f"LIMIT order {order.id} has no limit_price")
        if buy:
            return min(bar.open, lim) if bar.low < lim else None
        return max(bar.open, lim) if bar.high > lim else None
    trig = order.trigger_price
    if trig is None:
        raise ValueError(f"STOP order {order.id} has no trigger_price")
    if buy:
        return slip(max(bar.open, trig), order.side, slippage_bps) if bar.high >= trig else None
    return slip(min(bar.open, trig), order.side, slippage_bps) if bar.low <= trig else None
