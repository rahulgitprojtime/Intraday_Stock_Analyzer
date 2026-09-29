"""Simulated brokers — DECISIONS #29. SIMULATION ONLY.

`Broker` is the order interface strategies use. Only simulated
implementations exist: `BacktestBroker` (historical 1-min bars) and
`PaperBroker` (live Groww ticks). Both share `SimulatedBroker`, so fills,
costs, limits and square-off behave identically in backtest and paper mode.
There is deliberately no live implementation: nothing in this package
imports `src.broker` or the Groww SDK (tested).

Rules:
- An order may only fill on market events that arrive after it was placed
  (`active_seq`), so a signal on bar T's close fills at bar T+1 at the
  earliest. Bracket exits created by an entry fill are checked on the rest
  of that same bar (the fill was at its open).
- Within one event, STOP orders are checked before MARKET and LIMIT: when a
  bar touches both a stop and a target, the stop is assumed first.
- Long-only: a SELL may only reduce an existing position.
- Limits at placement: max position value per symbol, max open positions
  (held + pending-entry symbols), cash for the order plus its charges.
  Cash is checked again at the fill price.
- `square_off` cancels every open order, sells every position at the last
  price (less slippage) and blocks new entries until the next day.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

from src.data.models import Tick
from src.paper.costs import CostModel
from src.paper.fills import fill_price, slip
from src.paper.orders import Bar, Bracket, Fill, Order, OrderStatus, OrderType, Position, Side

PRIORITY = {OrderType.STOP: 0, OrderType.MARKET: 1, OrderType.LIMIT: 2}
AMBIGUOUS = "stop and target touched in one bar: stop assumed first"


@dataclass(frozen=True)
class BrokerConfig:
    starting_capital: float
    max_position_value: float
    max_open_positions: int
    slippage_bps: float
    square_off: time
    exchange: str = "NSE"

    @classmethod
    def from_dict(cls, d: dict) -> BrokerConfig:
        return cls(float(d["starting_capital"]), float(d["max_position_value"]),
                   int(d["max_open_positions"]), float(d["slippage_bps"]),
                   time.fromisoformat(str(d["square_off"])), str(d.get("exchange", "NSE")))


class Broker(ABC):
    """What a strategy may do. Implementations are simulated only."""

    @abstractmethod
    def place_order(self, symbol: str, side: Side, quantity: int,
                    order_type: OrderType = OrderType.MARKET, limit_price: float | None = None,
                    trigger_price: float | None = None, tag: str = "",
                    bracket: Bracket | None = None) -> Order: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def on_tick(self, event) -> list[Fill]: ...

    @abstractmethod
    def equity(self) -> float:
        """Cash plus open positions at their latest price."""


class SimulatedBroker(Broker):
    mode = "SIMULATED"

    def __init__(self, cfg: BrokerConfig, costs: CostModel, ledger=None, run_id: str = "run"):
        self.cfg, self.costs, self.ledger, self.run_id = cfg, costs, ledger, run_id
        self.cash = cfg.starting_capital
        self.trades: list[dict] = []
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._last: dict[str, float] = {}
        self._open_trades: dict[str, dict] = {}
        self._listeners: list[Callable[[Fill], None]] = []
        self._seq = 0
        self._n = 0
        self._clock: datetime | None = None
        self._day: date | None = None
        self._squared = False

    # -- queries ------------------------------------------------------------------

    def add_fill_listener(self, fn: Callable[[Fill], None]) -> None:
        self._listeners.append(fn)

    def positions(self) -> dict[str, Position]:
        return {s: p for s, p in self._positions.items() if p.quantity}

    def orders(self) -> list[Order]:
        return list(self._orders.values())

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        return [o for o in self._orders.values() if o.status is OrderStatus.OPEN
                and (symbol is None or o.symbol == symbol)]

    def last_price(self, symbol: str) -> float | None:
        return self._last.get(symbol)

    def equity(self) -> float:
        return self.cash + sum(p.quantity * self._last.get(s, p.avg_price)
                               for s, p in self._positions.items())

    @property
    def clock(self) -> datetime | None:
        return self._clock

    # -- orders -------------------------------------------------------------------

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, limit_price=None,
                    trigger_price=None, tag="", bracket=None) -> Order:
        o = Order(self._next_id(), symbol, Side(side), int(quantity), OrderType(order_type),
                  self._clock or datetime.min, limit_price=limit_price,
                  trigger_price=trigger_price, tag=tag, bracket=bracket,
                  exchange=self.cfg.exchange, active_seq=self._seq + 1)
        reason = self._check(o)
        if reason:
            o.status, o.reason = OrderStatus.REJECTED, reason
        self._orders[o.id] = o
        self._log_order(o)
        return o

    def cancel_order(self, order_id: str, reason: str = "cancelled") -> bool:
        o = self._orders.get(order_id)
        if o is None or o.status is not OrderStatus.OPEN:
            return False
        o.status, o.reason = OrderStatus.CANCELLED, reason
        self._log_order(o)
        return True

    # -- market events ------------------------------------------------------------

    def on_tick(self, event) -> list[Fill]:
        if isinstance(event, Tick):
            event = Bar.tick(event.symbol, event.ts, event.ltp)
        self._advance(event.ts)
        self._seq += 1
        fills: list[Fill] = []
        seen: set[str] = set()
        while True:
            live = [o for o in self._orders.values() if o.status is OrderStatus.OPEN
                    and o.symbol == event.symbol and o.active_seq <= self._seq
                    and o.id not in seen]
            if not live:
                break
            o = min(live, key=lambda o: (PRIORITY[o.type], o.id))
            seen.add(o.id)
            price = fill_price(o, event, self.cfg.slippage_bps)
            if price is None:
                continue
            f = self._execute(o, price, event.ts, self._ambiguity(o, event))
            if f is not None:
                fills.append(f)
        self._mark(event.symbol, event.close, event.ts)
        self._notify(fills)
        return fills

    def square_off(self, at: datetime, tag: str = "SQUARE_OFF") -> list[Fill]:
        self._advance(at)
        for o in self.open_orders():
            self.cancel_order(o.id, "square-off")
        fills = []
        for sym, pos in list(self.positions().items()):
            o = Order(self._next_id(), sym, Side.SELL, pos.quantity, OrderType.MARKET, at,
                      tag=tag, exchange=self.cfg.exchange, active_seq=self._seq)
            self._orders[o.id] = o
            price = slip(self._last.get(sym, pos.avg_price), Side.SELL, self.cfg.slippage_bps)
            f = self._execute(o, price, at, None)
            if f is not None:
                fills.append(f)
        self._squared = True
        self._notify(fills)
        return fills

    # -- internals ----------------------------------------------------------------

    def _next_id(self) -> str:
        self._n += 1
        return f"O{self._n:06d}"

    def _advance(self, ts: datetime) -> None:
        if self._day != ts.date():
            self._day, self._squared = ts.date(), False
        if self._clock is None or ts > self._clock:
            self._clock = ts

    def _ref_price(self, o: Order) -> float | None:
        if o.type is OrderType.LIMIT:
            return o.limit_price
        if o.type is OrderType.STOP:
            return o.trigger_price
        return self._last.get(o.symbol)

    def _check(self, o: Order) -> str | None:
        if o.quantity <= 0:
            return "quantity must be positive"
        if o.type is OrderType.LIMIT and not o.limit_price:
            return "LIMIT order needs limit_price"
        if o.type is OrderType.STOP and not o.trigger_price:
            return "STOP order needs trigger_price"
        held = self._positions.get(o.symbol)
        if o.side is Side.SELL:
            qty = held.quantity if held else 0
            if o.quantity > qty:
                return f"long-only: sell of {o.quantity} exceeds position {qty}"
            return None
        if self._squared or (self._clock and self._clock.time() >= self.cfg.square_off):
            return "no new entries after square-off"
        ref = self._ref_price(o)
        if ref is None:
            return f"no price seen yet for {o.symbol}"
        pending = [p for p in self.open_orders() if p.side is Side.BUY]
        est = o.quantity * ref
        value = (held.quantity * held.avg_price if held else 0.0) + est + sum(
            p.quantity * (self._ref_price(p) or 0) for p in pending if p.symbol == o.symbol)
        if value > self.cfg.max_position_value:
            return (f"position value {value:,.0f} would exceed max_position_value "
                    f"{self.cfg.max_position_value:,.0f}")
        in_use = set(self.positions()) | {p.symbol for p in pending}
        if o.symbol not in in_use and len(in_use) >= self.cfg.max_open_positions:
            return f"max open positions ({self.cfg.max_open_positions})"
        reserved = sum(p.quantity * (self._ref_price(p) or 0) for p in pending)
        need = est + self.costs.charges("BUY", o.quantity, ref, o.exchange).total
        if need > self.cash - reserved:
            return f"insufficient capital: needs {need:,.2f}, available {self.cash - reserved:,.2f}"
        return None

    def _ambiguity(self, o: Order, event: Bar) -> str | None:
        if o.type is not OrderType.STOP or o.oco_id is None:
            return None
        for s in self.open_orders(o.symbol):
            if s.oco_id == o.oco_id and s.id != o.id and \
                    fill_price(s, event, self.cfg.slippage_bps) is not None:
                return AMBIGUOUS
        return None

    def _execute(self, o: Order, price: float, at: datetime, note: str | None) -> Fill | None:
        pos = self._positions.setdefault(o.symbol, Position(o.symbol))
        q = o.quantity
        if o.side is Side.SELL and pos.quantity < q:
            self.cancel_order(o.id, "position already closed")
            return None
        ch = self.costs.charges(o.side.value, q, price, o.exchange)
        if o.side is Side.BUY:
            cost = q * price + ch.total
            if cost > self.cash + 1e-9:
                o.status, o.reason = OrderStatus.REJECTED, "insufficient capital at fill"
                self._log_order(o)
                return None
            pos.avg_price = (pos.avg_price * pos.quantity + price * q) / (pos.quantity + q)
            pos.quantity += q
            self.cash -= cost
            t = self._open_trades.setdefault(o.symbol, {
                "symbol": o.symbol, "entry_at": at.isoformat(), "entry_tag": o.tag,
                "buy_qty": 0, "buy_value": 0.0, "sell_qty": 0, "sell_value": 0.0,
                "charges": 0.0})
            t["buy_qty"] += q
            t["buy_value"] += q * price
        else:
            pos.realized_pnl += (price - pos.avg_price) * q
            pos.quantity -= q
            self.cash += q * price - ch.total
            t = self._open_trades[o.symbol]
            t["sell_qty"] += q
            t["sell_value"] += q * price
        pos.charges += ch.total
        t["charges"] += ch.total
        o.status, o.fill_price, o.filled_at = OrderStatus.FILLED, price, at
        f = Fill(o.id, o.symbol, o.side, q, price, at, ch, o.tag, note)
        self._log_order(o)
        if self.ledger is not None:
            self.ledger.add_fill(self.run_id, f)
            self.ledger.upsert_position(self.run_id, pos, at)
        if o.side is Side.BUY and o.bracket is not None:
            self._attach_bracket(o, price, at)
        if o.side is Side.SELL:
            for s in self.open_orders(o.symbol):
                if o.oco_id and s.oco_id == o.oco_id:
                    self.cancel_order(s.id, "OCO: other exit filled")
                elif pos.quantity == 0 and s.side is Side.SELL:
                    self.cancel_order(s.id, "position closed")
            if pos.quantity == 0:
                pos.avg_price = 0.0
                self._close_trade(o, at)
        return f

    def _attach_bracket(self, parent: Order, fill: float, at: datetime) -> None:
        stop, target = parent.bracket.levels(fill)
        kids = [Order(self._next_id(), parent.symbol, Side.SELL, parent.quantity, OrderType.STOP,
                      at, trigger_price=stop, tag="STOP_LOSS", parent_id=parent.id,
                      oco_id=parent.id, exchange=parent.exchange, active_seq=self._seq)]
        if target is not None:
            kids.append(Order(self._next_id(), parent.symbol, Side.SELL, parent.quantity,
                              OrderType.LIMIT, at, limit_price=target, tag="TARGET",
                              parent_id=parent.id, oco_id=parent.id, exchange=parent.exchange,
                              active_seq=self._seq))
        for k in kids:
            self._orders[k.id] = k
            self._log_order(k)

    def _close_trade(self, o: Order, at: datetime) -> None:
        t = self._open_trades.pop(o.symbol)
        gross = t["sell_value"] - t["buy_value"]
        entry_at = datetime.fromisoformat(t["entry_at"])
        rec = {"symbol": o.symbol, "entry_at": t["entry_at"], "exit_at": at.isoformat(),
               "quantity": t["buy_qty"], "entry_price": round(t["buy_value"] / t["buy_qty"], 4),
               "exit_price": round(t["sell_value"] / t["sell_qty"], 4),
               "gross_pnl": round(gross, 4), "charges": round(t["charges"], 4),
               "net_pnl": round(gross - t["charges"], 4), "entry_tag": t["entry_tag"],
               "exit_tag": o.tag, "holding_minutes": int((at - entry_at).total_seconds() // 60)}
        self.trades.append(rec)
        if self.ledger is not None:
            self.ledger.add_trade(self.run_id, rec)

    def persist_positions(self, at: datetime) -> None:
        """Write open positions (with their latest mark) to the ledger."""
        if self.ledger is not None:
            for p in self.positions().values():
                self.ledger.upsert_position(self.run_id, p, at)

    def mark(self, symbol: str, price: float) -> None:
        """Set the reference price without offering it as a fill event."""
        self._mark(symbol, price, None)

    def _mark(self, symbol: str, price: float, at: datetime | None) -> None:
        self._last[symbol] = price
        pos = self._positions.get(symbol)
        if pos is not None:
            pos.last_price = price

    def _notify(self, fills: list[Fill]) -> None:
        for f in fills:
            for fn in self._listeners:
                fn(f)

    def _log_order(self, o: Order) -> None:
        if self.ledger is not None:
            self.ledger.upsert_order(self.run_id, o)


class BacktestBroker(SimulatedBroker):
    """Fills against historical 1-min bars."""

    mode = "BACKTEST"

    def on_bar(self, candle) -> list[Fill]:
        return self.on_tick(Bar.from_candle(candle))


class PaperBroker(SimulatedBroker):
    """Fills against live Groww ticks (`Tick` or `Bar.tick`); nothing is sent."""

    mode = "PAPER"
