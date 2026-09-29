"""Intraday playbook — DECISIONS #30. SIMULATION ONLY. Long side only (#11).

Five setups from dhanith.com "Best intraday trading strategies", with the
vague parts made concrete (every number is in config/paper.yaml `playbook:`):

- ORB (5-min): first 5-min close above the first 15-min candle's high with
  volume above today's average 5-min volume; range width <= orb_max_range_pct;
  India VIX within [vix_min, vix_max] when VIX bars are available. Market
  entry. Stop: the range high (the source's rule: back inside = failed).
- VWAP (5-min): a 5-min close crosses above VWAP, volume above the previous
  5-min bar, and the index above its own session average. Market entry.
  Stop: VWAP less vwap_stop_buffer_pct.
- EMA_REJECTION (5-min, EMA 5/15 seeded from the prior session): both EMAs
  rising at least ema_min_slope_pct over ema_slope_bars bars, fast above
  slow; a bullish rejection candle (engulfing/hammer/pin bar) whose low
  touches the fast EMA and closes above the slow one. Buy-stop above its
  high for one bar. Stop: its low. Exit early on a 5-min close below EMA 15.
- BB_REVERSAL (15-min, Bollinger 20 / 1.5 incl. prior session): a bar that
  touches the lower band with its HIGH still above it (not a breakdown),
  green or a bullish reversal candle. Buy-stop above its high for one bar.
  Stop: its low.
- PDL_BOUNCE (15-min): a bullish reversal candle whose low is within
  level_near_pct of the previous day's low, volume above the prior session's
  average 15-min volume. Buy-stop above its high for one bar. Stop: the lower
  of its low and PDL. Target PDH, taken only if that is >= min_rr x risk.

Common rules (from the source): no entries before 09:30 (observation), risk
risk_pct of equity per trade, target min_rr x risk, at most
max_trades_per_day entries, stop for the day after max_consecutive_losses
losing trades, one trade per stock per day, flat by the broker's 15:15
square-off. Risk per share is floored at min_risk_pct of price. Candidates
come from the opening shortlist (src/paper/shortlist.py); signals in the
same minute are taken in shortlist-rank order. Starting values, not tuned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

from src.indicators.core import bollinger, ema
from src.paper.bars import BarAggregator, bullish_reversal
from src.paper.orders import Bar, Bracket, Fill, Order, OrderStatus, OrderType, Side
from src.paper.shortlist import ShortlistConfig, opening_shortlist
from src.paper.strategy import Strategy, StrategyContext

SETUPS = ("ORB", "VWAP", "EMA_REJECTION", "BB_REVERSAL", "PDL_BOUNCE")
FIVE_MIN = ("ORB", "VWAP", "EMA_REJECTION")


@dataclass(frozen=True)
class PlaybookConfig:
    setups: tuple = SETUPS
    risk_pct: float = 1.0
    min_rr: float = 2.0
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 2
    no_entry_before: time = time(9, 30)
    last_entry: time = time(14, 30)
    max_position_value: float = 25_000.0
    min_risk_pct: float = 0.15
    orb_max_range_pct: float = 2.0
    vix_min: float = 12.0
    vix_max: float = 18.0
    vwap_stop_buffer_pct: float = 0.2
    ema_fast: int = 5
    ema_slow: int = 15
    ema_slope_bars: int = 3
    ema_min_slope_pct: float = 0.1
    bb_period: int = 20
    bb_k: float = 1.5
    level_near_pct: float = 0.3
    index_symbol: str = "NIFTY"
    vix_symbol: str = "INDIAVIX"
    shortlist: ShortlistConfig = field(default_factory=ShortlistConfig)

    @classmethod
    def from_dict(cls, d: dict) -> PlaybookConfig:
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("no_entry_before", "last_entry"):
            if k in kw:
                kw[k] = time.fromisoformat(str(kw[k]))
        if "setups" in kw:
            bad = set(kw["setups"]) - set(SETUPS)
            if bad:
                raise ValueError(f"unknown playbook setups {sorted(bad)}; known {SETUPS}")
            kw["setups"] = tuple(kw["setups"])
        kw["shortlist"] = ShortlistConfig.from_dict(d.get("shortlist") or {})
        return cls(**kw)


@dataclass(frozen=True)
class Signal:
    setup: str
    ref: float                    # entry reference: signal close (market) or buy-stop trigger
    stop: float
    stop_entry: bool              # True: buy-stop at `ref`, valid for one bar of its timeframe
    target: float | None = None   # a level target (PDL_BOUNCE); else min_rr x risk
    minutes: int = 5


@dataclass
class _Sym:
    agg5: BarAggregator
    agg15: BarAggregator
    seen: int = 0
    pv: float = 0.0
    vol: float = 0.0
    tp_sum: float = 0.0
    n: int = 0
    vwap5: list = field(default_factory=list)

    def session_avg(self) -> float | None:
        """VWAP; the mean typical price when there is no volume (indices)."""
        if self.vol:
            return self.pv / self.vol
        return self.tp_sum / self.n if self.n else None


class IntradayPlaybook(Strategy):
    name = "PLAYBOOK"
    needs_prior = True

    def __init__(self, cfg: PlaybookConfig | None = None) -> None:
        self.cfg = cfg or PlaybookConfig()
        self.context_symbols = (self.cfg.index_symbol, self.cfg.vix_symbol)
        self.shortlist: list[dict] = []
        self._reset(None)

    def params(self) -> dict:
        d = asdict(self.cfg)
        return {k: (v.isoformat() if isinstance(v, time) else v) for k, v in d.items()}

    # -- day ----------------------------------------------------------------------

    def _reset(self, day: date | None) -> None:
        self.day = day
        self.shortlist = []
        self._syms: dict[str, _Sym] = {}
        self._listed = False
        self._pending: dict[str, tuple[Order, str, datetime]] = {}
        self._held: dict[str, dict] = {}
        self._traded: set[str] = set()
        self._orb_done: set[str] = set()
        self._filled = 0
        self._losses_in_row = 0
        self.halted: str | None = None
        self.signals: list[dict] = []           # every signal and what happened to it

    def on_day_start(self, day: date, ctx: StrategyContext) -> None:
        self._reset(day)

    def _state(self, sym: str, ctx: StrategyContext) -> _Sym:
        st = self._syms.get(sym)
        if st is None:
            st = self._syms[sym] = _Sym(BarAggregator(5), BarAggregator(15))
            prior = ctx.prior_bars(sym)
            st.agg5.seed(prior)
            st.agg15.seed(prior)
        return st

    # -- minute ---------------------------------------------------------------------

    def on_bars(self, as_of: datetime, bars: dict, ctx: StrategyContext) -> None:
        new5: dict[str, Bar] = {}
        new15: dict[str, Bar] = {}
        for sym in ctx.symbols():
            st = self._state(sym, ctx)
            for b in ctx.new_bars(sym, st.seen):
                st.seen += 1
                for agg, out in ((st.agg5, new5), (st.agg15, new15)):
                    closed = agg.add(b)                 # a gap closed the previous bucket
                    if closed:
                        out[sym] = closed
                        if agg is st.agg5:
                            st.vwap5.append(st.session_avg())   # before `b` counts
                tp = (b.high + b.low + b.close) / 3
                st.pv, st.vol = st.pv + tp * b.volume, st.vol + b.volume
                st.tp_sum, st.n = st.tp_sum + tp, st.n + 1
            for agg, out in ((st.agg5, new5), (st.agg15, new15)):
                closed = agg.flush(as_of)
                if closed:
                    out[sym] = closed
                    if agg is st.agg5:
                        st.vwap5.append(st.session_avg())
        self._expire(as_of, ctx)
        self._trend_exits(new5, ctx)
        if as_of.time() < self.cfg.no_entry_before:
            return
        if not self._listed:
            self._make_shortlist(ctx)
        if self.halted or as_of.time() > self.cfg.last_entry:
            return
        self._entries(as_of, new5, new15, ctx)

    def _make_shortlist(self, ctx: StrategyContext) -> None:
        skip = {self.cfg.index_symbol, self.cfg.vix_symbol}
        today = {s: ctx.bars(s) for s in ctx.symbols() if s not in skip}
        prior = {s: ctx.prior_bars(s) for s in today}
        self.shortlist = opening_shortlist(today, prior, self.cfg.shortlist)
        self._listed = True

    def _capacity(self) -> int:
        return self.cfg.max_trades_per_day - self._filled - len(self._pending)

    def _entries(self, as_of, new5, new15, ctx) -> None:
        signals = []
        for row in self.shortlist:
            sym = row["symbol"]
            if sym in self._traded or sym in self._pending or ctx.position(sym):
                continue
            sig = self._first_signal(sym, new5.get(sym), new15.get(sym), ctx)
            if sig is not None:
                signals.append((row["rank"], sym, sig))
        for rank, sym, sig in signals:                 # already in shortlist-rank order
            record = {"at": as_of.isoformat(), "symbol": sym, "setup": sig.setup,
                      "shortlist_rank": rank, "action": None}
            self.signals.append(record)
            if self._capacity() <= 0:
                record["action"] = f"skipped: max {self.cfg.max_trades_per_day} trades/day"
                continue
            record["action"] = self._place(sym, sig, as_of, ctx)

    def _place(self, sym: str, sig: Signal, as_of: datetime, ctx) -> str:
        c = self.cfg
        risk = max(sig.ref - sig.stop, sig.ref * c.min_risk_pct / 100)
        if sig.target is not None:
            reward = sig.target - sig.ref
            if reward < c.min_rr * risk:
                return f"skipped: level target under {c.min_rr:g}R"
        else:
            reward = c.min_rr * risk
        qty = min(int(ctx.broker.equity() * c.risk_pct / 100 // risk),
                  int(c.max_position_value // sig.ref))
        if qty < 1:
            return "skipped: size below 1 share"
        bracket = Bracket(round(risk, 4), round(reward, 4))
        if sig.stop_entry:
            o = ctx.broker.place_order(sym, Side.BUY, qty, OrderType.STOP,
                                       trigger_price=round(sig.ref, 4), tag=sig.setup,
                                       bracket=bracket)
        else:
            o = ctx.broker.place_order(sym, Side.BUY, qty, tag=sig.setup, bracket=bracket)
        if o.status is OrderStatus.REJECTED:
            return f"rejected: {o.reason}"
        expires = as_of + timedelta(minutes=sig.minutes) if sig.stop_entry else None
        self._pending[sym] = (o, sig.setup, expires)
        return "order placed"

    def _expire(self, as_of: datetime, ctx) -> None:
        for sym, (o, _, expires) in list(self._pending.items()):
            if o.status is not OrderStatus.OPEN:
                del self._pending[sym]                  # rejected/cancelled by the broker
            elif expires is not None and as_of >= expires:
                ctx.broker.cancel_order(o.id)
                del self._pending[sym]

    def _trend_exits(self, new5: dict, ctx) -> None:
        for sym, h in list(self._held.items()):
            if h["setup"] != "EMA_REJECTION" or sym not in new5:
                continue
            closes = [b.close for b in self._syms[sym].agg5.done]
            slow = ema(closes, self.cfg.ema_slow)[-1]
            pos = ctx.position(sym)
            if slow is not None and pos and new5[sym].close < slow and not h.get("exiting"):
                h["exiting"] = True
                ctx.broker.place_order(sym, Side.SELL, pos.quantity, tag="TREND_EXIT")

    # -- setups ---------------------------------------------------------------------

    def _first_signal(self, sym, b5, b15, ctx) -> Signal | None:
        for setup in self.cfg.setups:
            bar = b5 if setup in FIVE_MIN else b15
            if bar is None or bar.ts.date() != ctx.day:
                continue
            sig = getattr(self, f"_{setup.lower()}")(sym, bar, ctx)
            if sig is not None:
                return sig
        return None

    def _orb(self, sym, bar, ctx) -> Signal | None:
        c, st = self.cfg, self._syms[sym]
        if sym in self._orb_done:
            return None
        first15 = st.agg15.today(ctx.day)[:1]
        if not first15 or first15[0].ts.time() != time(9, 15) or bar.ts.time() < time(9, 30):
            return None
        hi, lo = first15[0].high, first15[0].low
        if (hi - lo) / hi * 100 > c.orb_max_range_pct:
            self._orb_done.add(sym)
            return None
        if bar.close <= hi:
            return None
        self._orb_done.add(sym)                         # first close above: one chance
        earlier = [b.volume for b in st.agg5.today(ctx.day)[:-1]]
        if not earlier or bar.volume <= sum(earlier) / len(earlier):
            return None
        vix = ctx.bars(c.vix_symbol)
        if vix and not c.vix_min <= vix[-1].close <= c.vix_max:
            return None
        return Signal("ORB", bar.close, hi, stop_entry=False)

    def _vwap(self, sym, bar, ctx) -> Signal | None:
        c, st = self.cfg, self._syms[sym]
        today5 = st.agg5.today(ctx.day)
        if len(today5) < 2 or len(st.vwap5) < 2 or None in st.vwap5[-2:]:
            return None
        prev, v_prev, v_now = today5[-2], st.vwap5[-2], st.vwap5[-1]
        if not (prev.close <= v_prev and bar.close > v_now and bar.volume > prev.volume):
            return None
        idx = self._syms.get(c.index_symbol)
        idx_bars = ctx.bars(c.index_symbol)
        if idx is None or not idx_bars or idx.session_avg() is None \
                or idx_bars[-1].close <= idx.session_avg():
            return None
        return Signal("VWAP", bar.close, v_now * (1 - c.vwap_stop_buffer_pct / 100),
                      stop_entry=False)

    def _ema_rejection(self, sym, bar, ctx) -> Signal | None:
        c, done = self.cfg, self._syms[sym].agg5.done
        k = c.ema_slope_bars
        if len(done) < c.ema_slow + k + 1:
            return None
        closes = [b.close for b in done]
        fast, slow = ema(closes, c.ema_fast), ema(closes, c.ema_slow)
        f, s, f0, s0 = fast[-1], slow[-1], fast[-1 - k], slow[-1 - k]
        rising = 1 + c.ema_min_slope_pct / 100
        if None in (f, s, f0, s0) or not (f > s and f >= f0 * rising and s >= s0 * rising):
            return None
        if not (bar.low <= f and bar.close > s and bullish_reversal(done[-2], bar)):
            return None
        return Signal("EMA_REJECTION", bar.high, bar.low, stop_entry=True, minutes=5)

    def _bb_reversal(self, sym, bar, ctx) -> Signal | None:
        c, done = self.cfg, self._syms[sym].agg15.done
        if len(done) < c.bb_period:
            return None
        _, _, lower = bollinger([b.close for b in done[-c.bb_period:]], c.bb_period, c.bb_k)
        lb = lower[-1]
        if not (bar.low <= lb < bar.high):
            return None
        if not (bar.close > bar.open or bullish_reversal(done[-2], bar)):
            return None
        return Signal("BB_REVERSAL", bar.high, bar.low, stop_entry=True, minutes=15)

    def _pdl_bounce(self, sym, bar, ctx) -> Signal | None:
        c, prior = self.cfg, ctx.prior_bars(sym)
        if not prior:
            return None
        pdl, pdh = min(b.low for b in prior), max(b.high for b in prior)
        if abs(bar.low - pdl) > pdl * c.level_near_pct / 100:
            return None
        done = self._syms[sym].agg15.done
        if not bullish_reversal(done[-2] if len(done) > 1 else None, bar):
            return None
        prior15 = [b for b in done if b.ts.date() != ctx.day]
        if not prior15 or bar.volume <= sum(b.volume for b in prior15) / len(prior15):
            return None
        return Signal("PDL_BOUNCE", bar.high, min(bar.low, pdl), stop_entry=True,
                      target=pdh, minutes=15)

    # -- fills ----------------------------------------------------------------------

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        sym = fill.symbol
        if fill.side is Side.BUY and sym in self._pending:
            _, setup, _ = self._pending.pop(sym)
            self._filled += 1
            self._traded.add(sym)
            self._held[sym] = {"setup": setup,
                               "cost": fill.quantity * fill.price + fill.charges.total,
                               "proceeds": 0.0}
        elif fill.side is Side.SELL and sym in self._held:
            h = self._held[sym]
            h["proceeds"] += fill.quantity * fill.price - fill.charges.total
            if ctx.position(sym) is None:              # round trip closed
                del self._held[sym]
                self._losses_in_row = self._losses_in_row + 1 \
                    if h["proceeds"] < h["cost"] else 0
                if self._losses_in_row >= self.cfg.max_consecutive_losses:
                    self.halted = f"{self._losses_in_row} losses in a row"
                    for o, _, _ in self._pending.values():
                        ctx.broker.cancel_order(o.id)
                    self._pending.clear()
