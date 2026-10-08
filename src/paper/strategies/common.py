"""Shared plumbing for the directional paper strategies — DECISIONS #31. SIMULATION ONLY.

- Market bias decides the side: BULLISH → longs only, BEARISH → shorts
  only, NEUTRAL → no new trades.
- Shorts reuse the long rules on a MIRRORED chart: every price p becomes
  2K − p around K = the stock's previous close (high and low swap). A
  breakdown then looks exactly like a breakout, so one tested rule set
  serves both sides. EMA, VWAP and Supertrend mirror exactly, RSI becomes
  100 − RSI, ADX and ranges are unchanged; percentage thresholds are
  approximately symmetric because prices stay near K.
- Only stocks moving with the bias are considered (long: above the previous
  close; short: below it).
- Size = risk budget / stop distance, capped by the position value limit.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.sentiment import SentimentConfig, market_sentiment, trade_direction
from src.paper.orders import Bar, Bracket, OrderStatus, OrderType, Side
from src.paper.strategy import Strategy, StrategyContext
from src.quantitative.daily_prep import DailyPrep

INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY")
TIME_EXIT = "TIME_EXIT"


def mirror_bar(b: Bar, k: float) -> Bar:
    return Bar(b.symbol, b.ts, 2 * k - b.open, 2 * k - b.low, 2 * k - b.high, 2 * k - b.close,
               b.volume)


def mirror_prep(p: DailyPrep, k: float) -> DailyPrep:
    return replace(p, prev_high=2 * k - p.prev_low, prev_low=2 * k - p.prev_high,
                   prev_close=2 * k - p.prev_close, pivot=2 * k - p.pivot,
                   cpr_top=2 * k - p.cpr_bottom, cpr_bottom=2 * k - p.cpr_top)


def to_candles(bars: list[Bar]) -> list[Candle]:
    """Strategy bars → the Candle type the indicator/setup library uses."""
    if not bars:
        return []
    inst = Instrument(bars[0].symbol, Exchange.NSE, Segment.CASH)
    return [Candle(inst, 1, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]


def market_bias(ctx: StrategyContext, cfg: SentimentConfig = SentimentConfig()) -> str:
    """The worker's sentiment when live; else computed from index bars in
    the context (backtests that include NIFTY / BANKNIFTY bars)."""
    sent = ctx.meta.get("sentiment")
    if sent:
        return sent.get("bias") or "NEUTRAL"
    indices = {}
    for name in INDEX_SYMBOLS:
        b = ctx.bars(name)
        if b:
            indices[name] = ([x.close for x in b], b[0].open, ctx.prev_close.get(name))
    return market_sentiment(indices, cfg)["bias"]


class DirectionalStrategy(Strategy):
    """Base: bias gate, per-symbol trade count, sizing, optional time exit.
    Subclasses implement `signal(sym, oriented_bars, k, ctx)` on bars already
    oriented so that "up" means "with the trade", returning
    (setup name, entry reference, stop distance) or None."""

    entry_tag = "ENTRY"

    def __init__(self, risk_per_trade: float, max_position_value: float, target_r: float,
                 max_trades_per_symbol: int, first_entry: time, last_entry: time,
                 max_hold_minutes: float | None = None,
                 sentiment_cfg: SentimentConfig = SentimentConfig()) -> None:
        self.risk_per_trade, self.max_position_value = risk_per_trade, max_position_value
        self.target_r, self.max_trades_per_symbol = target_r, max_trades_per_symbol
        self.first_entry, self.last_entry = first_entry, last_entry
        self.max_hold_minutes, self.sentiment_cfg = max_hold_minutes, sentiment_cfg
        self._trades: dict[str, int] = {}
        self._entered_at: dict[str, datetime] = {}
        self.signals: list[dict] = []          # every entry decision, for reports/tests

    # -- hooks ------------------------------------------------------------------------

    def on_day_start(self, day, ctx) -> None:
        self._trades, self._entered_at, self.signals = {}, {}, []

    def on_resume(self, orders, fills, ctx) -> None:
        for r in orders:
            if (r["tag"] or "").startswith(self.entry_tag) and r["status"] == "FILLED":
                self._trades[r["symbol"]] = self._trades.get(r["symbol"], 0) + 1
                if r["symbol"] in ctx.broker.positions() and r["filled_at"]:
                    self._entered_at[r["symbol"]] = datetime.fromisoformat(r["filled_at"])

    def on_fill(self, fill, ctx) -> None:
        if fill.tag.startswith(self.entry_tag):
            self._entered_at[fill.symbol] = fill.at

    def on_bars(self, as_of: datetime, bars: dict, ctx: StrategyContext) -> None:
        self._time_exits(as_of, ctx)
        direction = trade_direction(market_bias(ctx, self.sentiment_cfg))
        if direction is None or not self.first_entry <= as_of.time() <= self.last_entry:
            return
        indices = set(ctx.meta.get("indices") or ()) | set(INDEX_SYMBOLS)
        busy = set(ctx.broker.positions()) | {o.symbol for o in ctx.broker.open_orders()}
        candidates = []
        for sym in bars:
            if sym in indices or sym in busy:
                continue
            if self._trades.get(sym, 0) >= self.max_trades_per_symbol:
                continue
            history = ctx.bars(sym)
            k = ctx.prev_close.get(sym) or (history[0].open if history else None)
            if not history or not k:
                continue
            oriented = history if direction == "LONG" else [mirror_bar(b, k) for b in history]
            if oriented[-1].close <= k:                  # not moving with the market
                continue
            sig = self.signal(sym, oriented, k, ctx, direction)
            if sig is None:
                continue
            setup, ref, risk = sig
            qty = min(int(self.risk_per_trade // risk), int(self.max_position_value // ref))
            if risk <= 0 or qty < 1:
                continue
            # strength: move since the previous close in the trade's direction
            candidates.append(((oriented[-1].close / k - 1) * 100, sym, setup, risk, qty))
        # Slots are limited: the strongest movers get them first (name breaks ties).
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for strength, sym, setup, risk, qty in candidates:
            side = Side.BUY if direction == "LONG" else Side.SELL
            o = ctx.broker.place_order(sym, side, qty, OrderType.MARKET,
                                       tag=f"{self.entry_tag}:{setup}",
                                       bracket=Bracket(round(risk, 4),
                                                       round(self.target_r * risk, 4)))
            self.signals.append({"at": as_of.isoformat(), "symbol": sym, "direction": direction,
                                 "setup": setup, "risk": round(risk, 4), "qty": qty,
                                 "strength_pct": round(strength, 4),
                                 "status": o.status.value, "reason": o.reason})
            if o.status is not OrderStatus.REJECTED:
                self._trades[sym] = self._trades.get(sym, 0) + 1
                busy.add(sym)

    def signal(self, sym: str, bars: list[Bar], k: float, ctx: StrategyContext,
               direction: str):
        raise NotImplementedError

    # -- time stop ------------------------------------------------------------------

    def _time_exits(self, as_of: datetime, ctx: StrategyContext) -> None:
        if not self.max_hold_minutes:
            return
        limit = timedelta(minutes=self.max_hold_minutes)
        for sym, pos in ctx.broker.positions().items():
            t0 = self._entered_at.get(sym)
            if t0 is None or as_of - t0 < limit:
                continue
            if any(o.tag == TIME_EXIT for o in ctx.broker.open_orders(sym)):
                continue
            for o in ctx.broker.open_orders(sym):
                ctx.broker.cancel_order(o.id, "time exit")
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            ctx.broker.place_order(sym, side, abs(pos.quantity), OrderType.MARKET, tag=TIME_EXIT)
