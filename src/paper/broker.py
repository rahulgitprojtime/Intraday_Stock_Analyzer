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
- Positions are signed (long > 0, short < 0). An order that reduces the
  current position is an exit; anything else opens/adds. Opening a short
  (a SELL from flat) needs `allow_short` (DECISIONS #30); otherwise a SELL
  may only reduce a long. An exit may not flip the position.
- Bracket exits mirror for shorts: STOP buy above the fill, LIMIT buy below.
- Limits at placement (entries only): max position value per symbol, max
  open positions (held + pending-entry symbols), capital for the order plus
  its charges. A short blocks its full notional as margin, like a long
  (conservative: MIS leverage is ignored). Capital is checked again at the
  fill price.
- `square_off` cancels every open order, closes every position (sells
  longs, buys back shorts) at the last price less slippage and blocks new
  entries until the next day.
- `resume` rebuilds the broker from a ledger run (fills, open exit orders,
  closed trades) so a restarted worker keeps its positions and their stops
  and targets.
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
    allow_short: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> BrokerConfig:
        return cls(float(d["starting_capital"]), float(d["max_position_value"]),
                   int(d["max_open_positions"]), float(d["slippage_bps"]),
                   time.fromisoformat(str(d["square_off"])), str(d.get("exchange", "NSE")),
                   bool(d.get("allow_short", False)))


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
        """Cash plus the signed market value of positions (a short is a liability)."""
        return self.cash + sum(p.quantity * self._last.get(s, p.avg_price)
                               for s, p in self._positions.items())

    def _short_margin(self) -> float:
        """Notional of open shorts at entry. Their sale proceeds sit in cash, so
        twice this is held back: the proceeds plus the same again as margin."""
        return sum(-p.quantity * p.avg_price for p in self._positions.values() if p.quantity < 0)

    def _available(self) -> float:
        return self.cash - 2 * self._short_margin()

    @property
    def clock(self) -> datetime | None:
        return self._clock

    # -- orders -------------------------------------------------------------------

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, limit_price=None,
                    trigger_price=None, tag="", bracket=None) -> Order:
        side = Side(side)
        held = self._positions.get(symbol)
        q = held.quantity if held else 0
        opening = not (q and side.sign != (1 if q > 0 else -1))
        o = Order(self._next_id(), symbol, side, int(quantity), OrderType(order_type),
                  self._clock or datetime.min, limit_price=limit_price,
                  trigger_price=trigger_price, tag=tag, bracket=bracket,
                  exchange=self.cfg.exchange, active_seq=self._seq + 1, opening=opening)
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
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            o = Order(self._next_id(), sym, side, abs(pos.quantity), OrderType.MARKET, at,
                      tag=tag, exchange=self.cfg.exchange, active_seq=self._seq, opening=False)
            self._orders[o.id] = o
            price = slip(self._last.get(sym, pos.avg_price), side, self.cfg.slippage_bps)
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
        qty = held.quantity if held else 0
        if not o.opening:
            if o.quantity > abs(qty):
                return f"exit of {o.quantity} exceeds position {abs(qty)} (no flipping)"
            return None
        if o.side is Side.SELL and not self.cfg.allow_short:
            return f"long-only: sell of {o.quantity} exceeds position {qty}"
        if self._squared or (self._clock and self._clock.time() >= self.cfg.square_off):
            return "no new entries after square-off"
        ref = self._ref_price(o)
        if ref is None:
            return f"no price seen yet for {o.symbol}"
        pending = [p for p in self.open_orders() if p.opening]
        est = o.quantity * ref
        value = abs(qty) * (held.avg_price if held else 0.0) + est + sum(
            p.quantity * (self._ref_price(p) or 0) for p in pending if p.symbol == o.symbol)
        if value > self.cfg.max_position_value:
            return (f"position value {value:,.0f} would exceed max_position_value "
                    f"{self.cfg.max_position_value:,.0f}")
        in_use = set(self.positions()) | {p.symbol for p in pending}
        if o.symbol not in in_use and len(in_use) >= self.cfg.max_open_positions:
            return f"max open positions ({self.cfg.max_open_positions})"
        reserved = sum(p.quantity * (self._ref_price(p) or 0) for p in pending)
        need = est + self.costs.charges(o.side.value, o.quantity, ref, o.exchange).total
        avail = self._available() - reserved
        if need > avail:
            return f"insufficient capital: needs {need:,.2f}, available {avail:,.2f}"
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
        held_sign = (pos.quantity > 0) - (pos.quantity < 0)
        if not o.opening and (held_sign != -o.side.sign or abs(pos.quantity) < q):
            self.cancel_order(o.id, "position already closed")
            return None
        if o.opening and held_sign == -o.side.sign:
            self.cancel_order(o.id, "an opposite position is open")
            return None
        ch = self.costs.charges(o.side.value, q, price, o.exchange)
        if o.opening and q * price + ch.total > self._available() + 1e-9:
            o.status, o.reason = OrderStatus.REJECTED, "insufficient capital at fill"
            self._log_order(o)
            return None
        self._book(o, q, price, at, ch.total)
        o.status, o.fill_price, o.filled_at = OrderStatus.FILLED, price, at
        f = Fill(o.id, o.symbol, o.side, q, price, at, ch, o.tag, note)
        self._log_order(o)
        if self.ledger is not None:
            self.ledger.add_fill(self.run_id, f)
            self.ledger.upsert_position(self.run_id, pos, at)
        if o.opening and o.bracket is not None:
            self._attach_bracket(o, price, at)
        if not o.opening:
            for s in self.open_orders(o.symbol):
                if o.oco_id and s.oco_id == o.oco_id:
                    self.cancel_order(s.id, "OCO: other exit filled")
                elif pos.quantity == 0 and not s.opening:
                    self.cancel_order(s.id, "position closed")
            if pos.quantity == 0:
                self._close_trade(o, at)
        return f

    def _book(self, o: Order, q: int, price: float, at: datetime, charges: float) -> None:
        """Position, cash and round-trip accounting for one fill (no side effects)."""
        pos = self._positions.setdefault(o.symbol, Position(o.symbol))
        if o.opening:
            n = abs(pos.quantity)
            pos.avg_price = (pos.avg_price * n + price * q) / (n + q)
            pos.quantity += o.side.sign * q
            t = self._open_trades.setdefault(o.symbol, {
                "symbol": o.symbol, "direction": "LONG" if o.side is Side.BUY else "SHORT",
                "entry_at": at.isoformat(), "entry_tag": o.tag, "entry_qty": 0,
                "entry_value": 0.0, "exit_qty": 0, "exit_value": 0.0, "charges": 0.0,
                "stop_loss": None, "target": None})
            t["entry_qty"] += q
            t["entry_value"] += q * price
        else:
            pos.realized_pnl += (price - pos.avg_price) * q * (1 if pos.quantity > 0 else -1)
            pos.quantity += o.side.sign * q
            t = self._open_trades[o.symbol]
            t["exit_qty"] += q
            t["exit_value"] += q * price
            if pos.quantity == 0:
                pos.avg_price = 0.0
        self.cash += -o.side.sign * q * price - charges
        pos.charges += charges
        t["charges"] += charges

    def _attach_bracket(self, parent: Order, fill: float, at: datetime) -> None:
        stop, target = parent.bracket.levels(fill, parent.side)
        side = parent.side.opposite
        kids = [Order(self._next_id(), parent.symbol, side, parent.quantity, OrderType.STOP,
                      at, trigger_price=stop, tag="STOP_LOSS", parent_id=parent.id,
                      oco_id=parent.id, exchange=parent.exchange, active_seq=self._seq,
                      opening=False)]
        if target is not None:
            kids.append(Order(self._next_id(), parent.symbol, side, parent.quantity,
                              OrderType.LIMIT, at, limit_price=target, tag="TARGET",
                              parent_id=parent.id, oco_id=parent.id, exchange=parent.exchange,
                              active_seq=self._seq, opening=False))
        t = self._open_trades.get(parent.symbol)
        if t is not None:
            t["stop_loss"], t["target"] = stop, target
        for k in kids:
            self._orders[k.id] = k
            self._log_order(k)

    def _close_trade(self, o: Order, at: datetime) -> None:
        t = self._open_trades.pop(o.symbol)
        sign = 1 if t["direction"] == "LONG" else -1
        gross = sign * (t["exit_value"] - t["entry_value"])
        entry_at = datetime.fromisoformat(t["entry_at"])
        rec = {"symbol": o.symbol, "direction": t["direction"], "entry_at": t["entry_at"],
               "exit_at": at.isoformat(), "quantity": t["entry_qty"],
               "entry_price": round(t["entry_value"] / t["entry_qty"], 4),
               "exit_price": round(t["exit_value"] / t["exit_qty"], 4),
               "stop_loss": t["stop_loss"], "target": t["target"],
               "gross_pnl": round(gross, 4), "charges": round(t["charges"], 4),
               "net_pnl": round(gross - t["charges"], 4), "entry_tag": t["entry_tag"],
               "exit_tag": o.tag, "holding_minutes": int((at - entry_at).total_seconds() // 60)}
        self.trades.append(rec)
        if self.ledger is not None:
            self.ledger.add_trade(self.run_id, rec)

    def open_trades(self) -> list[dict]:
        """Open round trips with their stop/target (for the day report)."""
        return [dict(t) for t in self._open_trades.values()]

    def resume(self, orders: list[dict], fills: list[dict], trades: list[dict]) -> list[str]:
        """Rebuild state from an earlier ledger run of the same day (a worker
        restart): replay its fills, keep its closed trades, re-arm the open
        stop/target orders of open positions, cancel stale pending entries.
        Returns the symbols that still hold a position."""
        ledger, self.ledger = self.ledger, None          # replay without re-writing rows
        try:
            for r in fills:
                side = Side(r["side"])
                pos = self._positions.get(r["symbol"])
                q = pos.quantity if pos else 0
                opening = not (q and side.sign != (1 if q > 0 else -1))
                o = Order(r["order_id"], r["symbol"], side, int(r["quantity"]), OrderType.MARKET,
                          datetime.fromisoformat(r["at"]), tag=r["tag"] or "", opening=opening)
                at = datetime.fromisoformat(r["at"])
                self._book(o, o.quantity, float(r["price"]), at, float(r["total_charges"]))
                if not opening and self._positions[r["symbol"]].quantity == 0:
                    self._open_trades.pop(r["symbol"], None)
                self._last[r["symbol"]] = float(r["price"])
                self._advance(at)
            self.trades = [{k: v for k, v in t.items() if k != "run_id"} for t in trades]
            held = {s for s, p in self._positions.items() if p.quantity}
            for r in orders:
                self._n = max(self._n, int(r["id"].lstrip("O") or 0))
            for r in orders:
                if r["status"] != OrderStatus.OPEN.value:
                    continue
                if r["parent_id"] and r["symbol"] in held:
                    o = Order(r["id"], r["symbol"], Side(r["side"]), int(r["quantity"]),
                              OrderType(r["type"]), datetime.fromisoformat(r["placed_at"]),
                              limit_price=r["limit_price"], trigger_price=r["trigger_price"],
                              tag=r["tag"] or "", parent_id=r["parent_id"], oco_id=r["oco_id"],
                              exchange=self.cfg.exchange, active_seq=0, opening=False)
                    self._orders[o.id] = o
                    t = self._open_trades.get(o.symbol)
                    if t is not None:
                        key = "stop_loss" if o.type is OrderType.STOP else "target"
                        t[key] = o.trigger_price if o.type is OrderType.STOP else o.limit_price
                else:                                   # stale entry / orphan exit
                    o = Order(r["id"], r["symbol"], Side(r["side"]), int(r["quantity"]),
                              OrderType(r["type"]), datetime.fromisoformat(r["placed_at"]),
                              tag=r["tag"] or "", status=OrderStatus.CANCELLED,
                              reason="worker restart")
                    self._orders[o.id] = o
                    if ledger is not None:
                        ledger.upsert_order(self.run_id, o)
        finally:
            self.ledger = ledger
        return sorted(held)

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
