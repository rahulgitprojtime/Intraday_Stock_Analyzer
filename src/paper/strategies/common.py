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
- EntryRules (DECISIONS #33), each off unless configured: stop widened to a
  noise floor (1-min ATR, % of price); long only when beating NIFTY since
  the previous close, short only when lagging it; no entry against the
  entry timeframe's structure (lower swing highs on the oriented chart =
  higher lows on a short's real chart); skip a trade whose target profit
  is under `min_reward_cost_mult` x its round-trip charges; a daily trade
  cap; a stall exit freeing the slot of a position that has not moved
  `stall_r` x R in its favour within `stall_minutes`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.sentiment import SentimentConfig, market_sentiment, trade_direction
from src.paper.orders import Bar, Bracket, OrderStatus, OrderType, Side
from src.paper.strategy import Strategy, StrategyContext
from src.quantitative.daily_prep import DailyPrep

INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY")
TIME_EXIT = "TIME_EXIT"
STALL_EXIT = "STALL_EXIT"


@dataclass(frozen=True)
class EntryRules:
    """Shared entry/exit rules, each off at its default (DECISIONS #33)."""
    stop_atr_mult: float = 0.0          # stop >= this x ATR(14) of 1-min bars
    min_stop_pct: float = 0.0           # stop >= this % of the entry price
    rs_filter: bool = False             # long beats NIFTY / short lags it, since prev close
    structure_filter: bool = False      # no entry on lower swing highs (oriented chart)
    min_reward_cost_mult: float = 0.0   # target profit >= this x round-trip charges
    max_trades_per_day: int | None = None
    stall_minutes: float | None = None  # exit when not +stall_r x R by then
    stall_r: float = 0.5


def swing_points(bars) -> tuple[list[int], list[int]]:
    """Indices of 2-bar fractal swing highs and lows (needs 2 bars each side)."""
    highs, lows = [], []
    for i in range(2, len(bars) - 2):
        h, lo = bars[i].high, bars[i].low
        if all(h > bars[j].high for j in (i - 2, i - 1, i + 1, i + 2)):
            highs.append(i)
        if all(lo < bars[j].low for j in (i - 2, i - 1, i + 1, i + 2)):
            lows.append(i)
    return highs, lows


def lower_highs(bars, lookback: int = 20) -> bool:
    """The last two swing highs within `lookback` bars are falling."""
    window = bars[-lookback:]
    highs, _ = swing_points(window)
    return len(highs) >= 2 and window[highs[-1]].high < window[highs[-2]].high


def atr(bars, n: int = 14) -> float:
    """Average true range of the last `n` bars (fewer early in the day)."""
    tr = [b.high - b.low if i == 0 else
          max(b.high - b.low, abs(b.high - bars[i - 1].close), abs(b.low - bars[i - 1].close))
          for i, b in enumerate(bars)][-n:]
    return sum(tr) / len(tr) if tr else 0.0


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


def rules_from(cfg) -> EntryRules:
    """EntryRules from a strategy config's matching fields (absent = off)."""
    return EntryRules(**{k: getattr(cfg, k) for k in EntryRules.__dataclass_fields__
                         if hasattr(cfg, k)})


def nifty_change_pct(ctx: StrategyContext) -> float | None:
    """NIFTY % change vs its previous close: the worker's sentiment when live,
    else NIFTY bars in the context (backtests); None when unknown."""
    sent = ctx.meta.get("sentiment") or {}
    if sent.get("nifty_change_pct") is not None:
        return float(sent["nifty_change_pct"])
    b, prev = ctx.bars("NIFTY"), ctx.prev_close.get("NIFTY")
    return (b[-1].close / prev - 1) * 100 if b and prev else None


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
                 sentiment_cfg: SentimentConfig = SentimentConfig(),
                 rules: EntryRules = EntryRules()) -> None:
        self.risk_per_trade, self.max_position_value = risk_per_trade, max_position_value
        self.target_r, self.max_trades_per_symbol = target_r, max_trades_per_symbol
        self.first_entry, self.last_entry = first_entry, last_entry
        self.max_hold_minutes, self.sentiment_cfg = max_hold_minutes, sentiment_cfg
        self.rules = rules
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
        self._stall_exits(as_of, ctx)
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
            r = self.rules
            risk = max(risk, r.stop_atr_mult * atr(oriented), r.min_stop_pct / 100 * ref)
            qty = min(int(self.risk_per_trade // risk), int(self.max_position_value // ref)) \
                if risk > 0 else 0
            if qty < 1:
                continue
            # strength: move since the previous close in the trade's direction
            strength = (oriented[-1].close / k - 1) * 100
            why = self._veto(strength, direction, oriented, ref, risk, qty, ctx)
            if why:
                self.signals.append({"at": as_of.isoformat(), "symbol": sym,
                                     "direction": direction, "setup": setup,
                                     "risk": round(risk, 4), "qty": qty,
                                     "strength_pct": round(strength, 4),
                                     "status": "SKIPPED", "reason": why})
                continue
            candidates.append((strength, sym, setup, risk, qty))
        # Slots are limited: the strongest movers get them first (name breaks ties).
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for strength, sym, setup, risk, qty in candidates:
            if self.rules.max_trades_per_day is not None and \
                    sum(self._trades.values()) >= self.rules.max_trades_per_day:
                break
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

    def structure_bars(self, oriented: list[Bar]):
        """Bars of the entry timeframe for the structure filter (1-min here)."""
        return oriented

    # -- entry rules (DECISIONS #33) ---------------------------------------------------

    def _veto(self, strength, direction, oriented, ref, risk, qty, ctx) -> str | None:
        r = self.rules
        if r.rs_filter:
            nifty = nifty_change_pct(ctx)
            if nifty is not None:
                index_strength = nifty if direction == "LONG" else -nifty
                if strength <= index_strength:
                    return ("not beating NIFTY" if direction == "LONG"
                            else "not lagging NIFTY")
        if r.structure_filter and lower_highs(self.structure_bars(oriented)):
            return ("lower highs" if direction == "LONG"
                    else "higher lows (lower highs on the mirrored chart)")
        if r.min_reward_cost_mult:
            costs = ctx.broker.costs
            charges = (costs.charges("BUY", qty, ref).total
                       + costs.charges("SELL", qty, ref).total)
            reward = qty * self.target_r * risk
            if reward < r.min_reward_cost_mult * charges:
                return (f"target Rs {reward:.0f} < {r.min_reward_cost_mult:g} x "
                        f"charges Rs {charges:.0f}")
        return None

    # -- time and stall exits ----------------------------------------------------------

    def _exit(self, sym, pos, ctx, tag) -> None:
        if any(o.tag in (TIME_EXIT, STALL_EXIT) for o in ctx.broker.open_orders(sym)):
            return
        for o in ctx.broker.open_orders(sym):
            ctx.broker.cancel_order(o.id, tag.lower().replace("_", " "))
        side = Side.SELL if pos.quantity > 0 else Side.BUY
        ctx.broker.place_order(sym, side, abs(pos.quantity), OrderType.MARKET, tag=tag)

    def _stall_exits(self, as_of: datetime, ctx: StrategyContext) -> None:
        """Free the slot of a position that has not moved stall_r x R its way."""
        r = self.rules
        if not r.stall_minutes:
            return
        for sym, pos in ctx.broker.positions().items():
            t0 = self._entered_at.get(sym)
            if t0 is None or as_of - t0 < timedelta(minutes=r.stall_minutes):
                continue
            stops = [o.trigger_price for o in ctx.broker.open_orders(sym)
                     if o.type is OrderType.STOP and o.trigger_price]
            since = [b for b in ctx.bars(sym) if b.ts >= t0]
            if not stops or not since:
                continue
            risk = abs(pos.avg_price - stops[0])
            best = (max(b.high for b in since) - pos.avg_price if pos.quantity > 0
                    else pos.avg_price - min(b.low for b in since))
            if risk > 0 and best < r.stall_r * risk:
                self._exit(sym, pos, ctx, STALL_EXIT)

    def _time_exits(self, as_of: datetime, ctx: StrategyContext) -> None:
        if not self.max_hold_minutes:
            return
        limit = timedelta(minutes=self.max_hold_minutes)
        for sym, pos in ctx.broker.positions().items():
            t0 = self._entered_at.get(sym)
            if t0 is None or as_of - t0 < limit:
                continue
            self._exit(sym, pos, ctx, TIME_EXIT)
