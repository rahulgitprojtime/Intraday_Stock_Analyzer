"""Opening Range Breakout, long and short — sample strategy, DECISIONS #29/#30.

Opening range = high/low of the first `range_minutes` 1-min bars from
09:15. The first bar that CLOSES above the range high triggers a market
BUY (filled at the next bar's open by the broker) with a bracket:

- stop distance = signal close - range low (the stop sits at about the
  range low; distances are fixed at the signal, applied to the fill);
- target = `target_r` x the stop distance.

With `allow_short`, the first bar that CLOSES below the range low
triggers a market SELL (short) instead, mirrored: stop distance = range
high - signal close (stop about the range high, above the fill), target
`target_r` x that distance below the fill. Whichever side breaks first is
the day's one attempt for that symbol.

Size = floor(risk_per_trade / stop distance), capped so the position stays
within `max_position_value`. At most one attempt per symbol per day; no
entries after `last_entry`; days whose range is wider than `max_range_pct`
are skipped (the stop would be too far). Starting values, not tuned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from src.data.models import SESSION_OPEN
from src.paper.orders import Bar, Bracket, OrderType, Side
from src.paper.strategy import Strategy, StrategyContext

ENTRY_TAG = "ORB_ENTRY"


@dataclass
class OpeningRangeBreakout(Strategy):
    range_minutes: int = 15
    target_r: float = 2.0
    risk_per_trade: float = 500.0
    max_position_value: float = 25_000.0
    max_range_pct: float = 3.0
    last_entry: time = time(14, 30)
    symbols: tuple[str, ...] | None = None          # None: every symbol with bars
    allow_short: bool = False
    name = "ORB"
    _done: set = field(default_factory=set, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> OpeningRangeBreakout:
        return cls(int(d.get("range_minutes", 15)), float(d.get("target_r", 2.0)),
                   float(d.get("risk_per_trade", 500.0)),
                   float(d.get("max_position_value", 25_000.0)),
                   float(d.get("max_range_pct", 3.0)),
                   time.fromisoformat(str(d.get("last_entry", "14:30"))),
                   tuple(d["symbols"]) if d.get("symbols") else None,
                   bool(d.get("allow_short", False)))

    def params(self) -> dict:
        return {"range_minutes": self.range_minutes, "target_r": self.target_r,
                "risk_per_trade": self.risk_per_trade,
                "max_position_value": self.max_position_value,
                "max_range_pct": self.max_range_pct, "last_entry": self.last_entry.isoformat(),
                "symbols": list(self.symbols) if self.symbols else None,
                "allow_short": self.allow_short}

    def on_day_start(self, day: date, ctx: StrategyContext) -> None:
        self._done = set()

    def on_resume(self, orders: list[dict], fills: list[dict], ctx: StrategyContext) -> None:
        self._done |= {r["symbol"] for r in orders if r["tag"] == ENTRY_TAG}

    def on_bars(self, as_of: datetime, bars: dict[str, Bar], ctx: StrategyContext) -> None:
        if as_of.time() > self.last_entry:
            return
        for sym, bar in sorted(bars.items()):
            if sym in self._done or (self.symbols and sym not in self.symbols):
                continue
            end = datetime.combine(bar.ts.date(), SESSION_OPEN) + \
                timedelta(minutes=self.range_minutes)
            if bar.ts < end:
                continue                                  # range still forming
            rng = [b for b in ctx.bars(sym) if b.ts < end]
            if not rng or rng[0].ts.time() != SESSION_OPEN:
                self._done.add(sym)                       # incomplete range: no trade today
                continue
            hi, lo = max(b.high for b in rng), min(b.low for b in rng)
            if (hi - lo) / hi * 100 > self.max_range_pct:
                self._done.add(sym)
                continue
            if bar.close > hi:
                side, risk = Side.BUY, bar.close - lo
            elif self.allow_short and bar.close < lo:
                side, risk = Side.SELL, hi - bar.close
            else:
                continue
            self._done.add(sym)
            if risk <= 0:
                continue
            qty = min(int(self.risk_per_trade // risk), int(self.max_position_value // bar.close))
            if qty < 1:
                continue
            ctx.broker.place_order(sym, side, qty, OrderType.MARKET, tag=ENTRY_TAG,
                                   bracket=Bracket(round(risk, 4),
                                                   round(self.target_r * risk, 4)))
