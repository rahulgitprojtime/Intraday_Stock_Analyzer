"""Whole-market setup study, long and short — DECISIONS #31. SIMULATION ONLY.

Research code: never imports src/broker or the SDK and never places orders.
For one stock-day it builds 5/10/15/30-min candles from the stored 1-min
bars (a candle counts only once it has closed), finds the FIRST signal of
each setup, and simulates every exit variant on the 1-min bars after the
entry with the fill rules of src/paper/fills.py and Groww charges.

Shorts are longs on the mirror image (every price p -> -p: highs and lows
swap, VWAP/EMA/Bollinger mirror exactly), so one rule set serves both sides.
Percentages use abs(price). Times are minutes since 09:15: the bar at t
covers [t, t+1); a candle of m minutes starting at t closes at t+m (the
last one at 375 = 15:30).
"""

from __future__ import annotations

import csv
import os
from bisect import bisect_left
from collections import defaultdict, namedtuple
from dataclasses import dataclass, replace
from pathlib import Path

from src.indicators.core import bollinger, ema
from src.paper.costs import CostModel

K = namedtuple("K", "t o h l c v")

SETUPS = ("ORB", "VWAP", "EMA", "BB", "LEVEL")
SIDES = ("LONG", "SHORT")
EXITS = {"NATIVE": None, "T1": 1.0, "T2": 2.0, "T5": 5.0, "T10": 10.0}
TIMEFRAMES = (5, 10, 15, 30)
SESSION_END = 375
SQUARE_OFF = 360               # 15:15
FIRST_SIGNAL = 15              # 09:30 (ORB's range makes its earliest close 09:25)
LAST_SIGNAL = 315              # 14:30
POSITION_VALUE = 25_000.0
SLIPPAGE_BPS = 5.0
ORB_ATR_STOP = 0.10
EMA_FAST, EMA_SLOW, EMA_SLOPE_BARS, EMA_SLOPE_PCT = 5, 15, 3, 0.1
BB_PERIOD, BB_K = 20, 2.0
LEVEL_NEAR_PCT = 0.3
MTF_EMA = 20
REGIME_PCT = 0.25
PASS_TRADES, PASS_PF = 100, 1.1

_SHORT_NAMES = {"HAMMER": "SHOOTING_STAR", "ENGULFING": "BEAR_ENGULFING",
                "PIN_BAR": "BEAR_PIN_BAR", "MARUBOZU": "BEAR_MARUBOZU"}


# -- candles ---------------------------------------------------------------------------

def read_bars(path: str | Path) -> list[K]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        next(rows, None)
        for r in rows:
            if len(r) < 7 or r[6] != "1":
                continue
            ts = r[0]
            t = int(ts[11:13]) * 60 + int(ts[14:16]) - 555
            if 0 <= t < SESSION_END:
                out.append(K(t, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                             float(r[5] or 0)))
    out.sort()
    return out


def aggregate(bars: list[K], minutes: int) -> list[K]:
    out, cur = [], None
    for b in bars:
        s = b.t - b.t % minutes
        if cur is None or s != cur[0]:
            if cur is not None:
                out.append(K(*cur))
            cur = [s, b.o, b.h, b.l, b.c, b.v]
        else:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], b.h), min(cur[3], b.l), b.c, cur[5] + b.v
    if cur is not None:
        out.append(K(*cur))
    return out


def mirror(bars: list[K]) -> list[K]:
    return [K(b.t, -b.o, -b.l, -b.h, -b.c, b.v) for b in bars]


def end(c: K, minutes: int) -> int:
    return min(c.t + minutes, SESSION_END)


def pattern(prev: K | None, cur: K) -> str | None:
    """Bullish candle on `cur` (apply to the mirror for bearish): ENGULFING,
    HAMMER, PIN_BAR or MARUBOZU (thresholds in DECISIONS #31)."""
    rng = cur.h - cur.l
    if rng <= 0:
        return None
    body = abs(cur.c - cur.o)
    lower = min(cur.o, cur.c) - cur.l
    upper = cur.h - max(cur.o, cur.c)
    if prev is not None and prev.c < prev.o and cur.c > cur.o \
            and cur.c >= prev.o and cur.o <= prev.c:
        return "ENGULFING"
    if lower >= 2 * body and upper <= body and cur.c >= cur.l + 0.6 * rng:
        return "HAMMER"
    if lower >= rng * 2 / 3:
        return "PIN_BAR"
    if cur.c > cur.o and body >= 0.6 * rng and cur.c >= cur.h - 0.25 * rng:
        return "MARUBOZU"
    return None


def pattern_name(side: str, name: str | None) -> str | None:
    return _SHORT_NAMES.get(name, name) if side == "SHORT" else name


def running_avg(bars: list[K]) -> list[float | None]:
    """[T] = session VWAP (typical price) over the bars closed by minute T;
    the mean typical price when there is no volume (indices)."""
    out: list[float | None] = [None] * (SESSION_END + 1)
    pv = vol = tps = 0.0
    n = j = 0
    for T in range(1, SESSION_END + 1):
        while j < len(bars) and bars[j].t + 1 <= T:
            b = bars[j]
            tp = (b.h + b.l + b.c) / 3
            pv, vol, tps, n, j = pv + tp * b.v, vol + b.v, tps + tp, n + 1, j + 1
        out[T] = pv / vol if vol else (tps / n if n else None)
    return out


def last_close(bars: list[K]) -> list[float | None]:
    out: list[float | None] = [None] * (SESSION_END + 1)
    j, last = 0, None
    for T in range(1, SESSION_END + 1):
        while j < len(bars) and bars[j].t + 1 <= T:
            last, j = bars[j].c, j + 1
        out[T] = last
    return out


# -- day input -------------------------------------------------------------------------

@dataclass(frozen=True)
class DayInput:
    bars: list                      # today's 1-min bars
    agg: dict                       # minutes -> today's candles
    prior: dict                     # minutes -> prior sessions' candles, oldest first
    index: list                     # NIFTY 1-min bars today
    pdh: float | None = None
    pdl: float | None = None
    pdc: float | None = None
    atr: float | None = None        # 20-session mean true range (a distance)
    daily_trend: int | None = None  # sign(previous close - EMA20 of daily closes)

    def mirrored(self) -> DayInput:
        neg = lambda x: None if x is None else -x  # noqa: E731
        return DayInput(mirror(self.bars), {m: mirror(c) for m, c in self.agg.items()},
                        {m: mirror(c) for m, c in self.prior.items()}, mirror(self.index),
                        neg(self.pdl), neg(self.pdh), neg(self.pdc), self.atr,
                        None if self.daily_trend is None else -self.daily_trend)


def make_day(bars: list[K], prior_sessions: list[list[K]], index: list[K], **kw) -> DayInput:
    agg = {m: aggregate(bars, m) for m in TIMEFRAMES}
    prior = {m: [c for s in prior_sessions for c in aggregate(s, m)] for m in TIMEFRAMES}
    return DayInput(bars, agg, prior, index, **kw)


# -- setups (long view) ------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    setup: str
    t: int                          # minute the signal candle closed
    ref: float                      # its close (sizing)
    stop: float | None              # a price level, or None: atr_stop from the fill
    atr_stop: float | None
    pattern: str | None
    target: float | None = None     # NATIVE level target (used only beyond the entry)
    trend: tuple = ()               # NATIVE trend exit: (close time, close, level), exit if close < level
    mtf: bool = False


def _window(T: int) -> int:
    """-1 too early, 0 inside the signal window, 1 past the last entry."""
    return -1 if T < FIRST_SIGNAL else (1 if T > LAST_SIGNAL else 0)


def _orb(d: DayInput, candle: bool) -> Signal | None:
    c5 = d.agg[5]
    if not c5 or c5[0].t != 0 or not c5[0].c > c5[0].o:
        return None
    hi = c5[0].h
    for i in range(1, len(c5)):
        b, T = c5[i], end(c5[i], 5)
        if T > LAST_SIGNAL:
            return None
        if b.c > hi:                                   # the first close beyond: one chance
            pat = pattern(c5[i - 1], b)
            if (candle and not pat) or d.atr is None:
                return None
            return Signal("ORB", T, b.c, None, ORB_ATR_STOP * d.atr, pat)
    return None


def _vwap(d: DayInput, candle: bool) -> Signal | None:
    c5, vw = d.agg[5], running_avg(d.bars)
    ix_avg, ix_last = running_avg(d.index), last_close(d.index)
    for i in range(1, len(c5)):
        prev, b = c5[i - 1], c5[i]
        T, Tp = end(b, 5), end(prev, 5)
        w = _window(T)
        if w > 0:
            return None
        if w < 0 or vw[Tp] is None or vw[T] is None:
            continue
        if not (prev.c <= vw[Tp] and b.c > vw[T]):
            continue
        if ix_last[T] is None or ix_avg[T] is None or not ix_last[T] > ix_avg[T]:
            continue
        pat = pattern(prev, b)
        if candle and not pat:
            continue
        trend = tuple((end(x, 5), x.c, vw[end(x, 5)]) for x in c5[i + 1:])
        return Signal("VWAP", T, b.c, b.l, None, pat, trend=trend)
    return None


def _ema(d: DayInput, candle: bool) -> Signal | None:
    p5, c5 = d.prior[5], d.agg[5]
    allc = p5 + c5
    closes = [x.c for x in allc]
    f, s, off, k = ema(closes, EMA_FAST), ema(closes, EMA_SLOW), len(p5), EMA_SLOPE_BARS
    slope = EMA_SLOPE_PCT / 100
    for j, b in enumerate(c5):
        i, T = off + j, end(b, 5)
        w = _window(T)
        if w > 0:
            return None
        if w < 0 or i < k or None in (f[i], s[i], f[i - k], s[i - k]):
            continue
        if not (f[i] > s[i] and f[i] - f[i - k] >= slope * abs(f[i - k])
                and s[i] - s[i - k] >= slope * abs(s[i - k])):
            continue
        if not (b.l <= f[i] and b.c > s[i]):
            continue
        pat = pattern(allc[i - 1] if i else None, b)
        if candle and not pat:
            continue
        trend = tuple((end(x, 5), x.c, s[off + jj]) for jj, x in enumerate(c5) if jj > j)
        return Signal("EMA", T, b.c, b.l, None, pat, trend=trend)
    return None


def _bb(d: DayInput, candle: bool) -> Signal | None:
    p15, c15 = d.prior[15], d.agg[15]
    allc = p15 + c15
    mid, _, lower = bollinger([x.c for x in allc], BB_PERIOD, BB_K)
    for j, b in enumerate(c15):
        i, T = len(p15) + j, end(b, 15)
        w = _window(T)
        if w > 0:
            return None
        if w < 0 or lower[i] is None or not (b.l <= lower[i] < b.c):
            continue
        pat = pattern(allc[i - 1] if i else None, b)
        if candle and not pat:
            continue
        return Signal("BB", T, b.c, b.l, None, pat, target=mid[i])
    return None


def _level(d: DayInput, candle: bool) -> Signal | None:
    if d.pdl is None:
        return None
    c15 = d.agg[15]
    near = d.pdl + LEVEL_NEAR_PCT / 100 * abs(d.pdl)
    for j, b in enumerate(c15):
        T = end(b, 15)
        w = _window(T)
        if w > 0:
            return None
        if w < 0 or not (b.l <= near and b.c > d.pdl):
            continue
        prev = c15[j - 1] if j else (d.prior[15][-1] if d.prior[15] else None)
        pat = pattern(prev, b)
        if candle and not pat:
            continue
        return Signal("LEVEL", T, b.c, b.l, None, pat, target=d.pdc)
    return None


def mtf_aligned(d: DayInput, T: int) -> bool:
    """10-min, 30-min and daily trends all up (in this view) at minute T."""
    if not d.daily_trend or d.daily_trend <= 0:
        return False
    for m in (10, 30):
        closes = [c.c for c in d.prior[m]] + [c.c for c in d.agg[m] if end(c, m) <= T]
        e = ema(closes, MTF_EMA)
        if not closes or e[-1] is None or not closes[-1] > e[-1]:
            return False
    return True


def detect(d: DayInput, candle: bool = True) -> list[Signal]:
    """First signal of each setup in this view (mirror the day for shorts)."""
    out = []
    for fn in (_orb, _vwap, _ema, _bb, _level):
        sig = fn(d, candle)
        if sig is not None:
            out.append(replace(sig, mtf=mtf_aligned(d, sig.t)))
    return out


# -- fills and exits -----------------------------------------------------------------------

@dataclass(frozen=True)
class Exit:
    t: int
    price: float                    # the fill, slippage included
    reason: str
    raw: float | None = None        # the same exit before slippage (for cost accounting)


def _buy(px: float) -> float:
    """Pay the slippage; the same arithmetic as fills.slip, also for a
    mirrored (negative) price, whose real order is a sell."""
    s = SLIPPAGE_BPS / 10_000
    return round(px * (1 + s if px >= 0 else 1 - s), 4)


def _sell(px: float) -> float:
    s = SLIPPAGE_BPS / 10_000
    return round(px * (1 - s if px >= 0 else 1 + s), 4)


def enter(sig: Signal, bars: list[K]) -> tuple[int, float, float] | None:
    """(entry bar index, fill, stop): market at the first open at/after the
    signal close, plus slippage. None: no bar before 15:15, or the stop is
    not below the fill."""
    i = bisect_left([b.t for b in bars], sig.t)
    if i >= len(bars) or bars[i].t >= SQUARE_OFF:
        return None
    fill = _buy(bars[i].o)
    stop = fill - sig.atr_stop if sig.atr_stop is not None else sig.stop
    if stop is None or stop >= fill:
        return None
    return i, fill, stop


def exit_trade(bars: list[K], i: int, entry: float, stop: float, target: float | None = None,
               trend: tuple = ()) -> Exit:
    """Walk the 1-min bars from the entry bar. Per bar: stop first (touch,
    gap fills at the open), then a pending trend exit (market at the open),
    then the target (limit, trade-through only). Flat at 15:15."""
    pend = next((T for T, c, lvl in trend if T > bars[i].t and lvl is not None and c < lvl), None)
    last = entry
    for b in bars[i:]:
        if b.t >= SQUARE_OFF:
            break
        if b.l <= stop:
            return Exit(b.t, _sell(min(b.o, stop)), "STOP")
        if pend is not None and b.t >= pend:
            return Exit(b.t, _sell(b.o), "TREND")
        if target is not None and b.h > target:
            return Exit(b.t, round(max(b.o, target), 4), "TARGET")
        last = b.c
    return Exit(SQUARE_OFF, _sell(last), "SQUARE_OFF")


def settle(side: str, qty: int, entry: float, exit_px: float, costs: CostModel,
           exchange: str = "NSE") -> tuple[float, float, float]:
    """(gross, charges, net) from view prices. A short sells first at -entry
    and covers with a buy at -exit."""
    gross = (exit_px - entry) * qty
    if side == "LONG":
        ch = costs.charges("BUY", qty, entry, exchange).total + \
            costs.charges("SELL", qty, exit_px, exchange).total
    else:
        ch = costs.charges("SELL", qty, -entry, exchange).total + \
            costs.charges("BUY", qty, -exit_px, exchange).total
    return round(gross, 4), round(ch, 4), round(gross - ch, 4)


# -- one stock-day -----------------------------------------------------------------------

def clock(t: int) -> str:
    h, m = divmod(555 + t, 60)
    return f"{h:02d}:{m:02d}"


def regime(move_pct: float | None) -> str:
    if move_pct is None:
        return "UNKNOWN"
    return "UP" if move_pct > REGIME_PCT else ("DOWN" if move_pct < -REGIME_PCT else "SIDEWAYS")


COLUMNS = ["day", "symbol", "setup", "side", "candle", "pattern", "signal_time", "entry_time",
           "entry", "stop", "qty", "regime", "day_type", "rvol5", "mtf"] + \
    [f"{e}_{k}" for e in EXITS for k in ("time", "price", "reason", "net", "charges")]


def study_stock_day(day: str, symbol: str, d: DayInput, costs: CostModel,
                    rvol5: float | None, counts: dict) -> list[dict]:
    idx = d.index
    open_ix = idx[0].o if idx else None
    ix_close = last_close(idx)
    day_move = (idx[-1].c / open_ix - 1) * 100 if idx and open_ix else None
    rows = []
    for side in SIDES:
        v = d if side == "LONG" else d.mirrored()
        sgn = 1 if side == "LONG" else -1
        for candle in (True, False):
            for sig in detect(v, candle):
                e = enter(sig, v.bars)
                qty = int(POSITION_VALUE // abs(sig.ref))
                if e is None or qty < 1:
                    counts["skipped"] += 1
                    continue
                i, entry, stop = e
                at = v.bars[i].t
                move = (ix_close[at] / open_ix - 1) * 100 if open_ix and ix_close[at] else None
                row = {"day": day, "symbol": symbol, "setup": sig.setup, "side": side,
                       "candle": int(candle), "pattern": pattern_name(side, sig.pattern) or "",
                       "signal_time": clock(sig.t), "entry_time": clock(at),
                       "entry": sgn * entry, "stop": round(sgn * stop, 4), "qty": qty,
                       "regime": regime(move), "day_type": regime(day_move),
                       "rvol5": "" if rvol5 is None else round(rvol5, 3), "mtf": int(sig.mtf)}
                for name, pct in EXITS.items():
                    if not candle and name != "NATIVE":
                        continue
                    if pct is None:
                        target = sig.target if sig.target is not None and sig.target > entry else None
                        x = exit_trade(v.bars, i, entry, stop, target, sig.trend)
                    else:
                        x = exit_trade(v.bars, i, entry, stop, entry + abs(entry) * pct / 100)
                    _, ch, net = settle(side, qty, entry, x.price, costs)
                    row |= {f"{name}_time": clock(x.t), f"{name}_price": sgn * x.price,
                            f"{name}_reason": x.reason, f"{name}_net": net, f"{name}_charges": ch}
                rows.append(row)
                counts["signals"] += 1
    return rows


# -- store -------------------------------------------------------------------------------

class DailyHistory:
    """Daily candles of one stock; every value is taken from sessions
    strictly before the asked day."""

    def __init__(self, path: Path) -> None:
        self.dates, self.h, self.l, self.c, self.v = [], [], [], [], []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    self.dates.append(r["date"])
                    self.h.append(float(r["high"]))
                    self.l.append(float(r["low"]))
                    self.c.append(float(r["close"]))
                    self.v.append(float(r["volume"]))
        self.ema = ema(self.c, MTF_EMA)

    def before(self, day: str, sessions: int = 20) -> dict:
        k = bisect_left(self.dates, day)
        if k == 0:
            return {}
        out = {"pdh": self.h[k - 1], "pdl": self.l[k - 1], "pdc": self.c[k - 1]}
        e = self.ema[k - 1]
        if e is not None:
            out["daily_trend"] = (self.c[k - 1] > e) - (self.c[k - 1] < e)
        if k >= sessions:
            trs = [self.h[j] - self.l[j] if j == k - sessions else
                   max(self.h[j] - self.l[j], abs(self.h[j] - self.c[j - 1]),
                       abs(self.l[j] - self.c[j - 1])) for j in range(k - sessions, k)]
            out["atr"] = sum(trs) / sessions
            out["avg_volume"] = sum(self.v[k - sessions:k]) / sessions
        return out


def store_days(root: str | Path) -> list[str]:
    return sorted(p.name for p in Path(root).iterdir()
                  if p.is_dir() and len(p.name) == 10 and p.name[4] == "-")


def iter_days(store: str, days: list[str], prior_days: list[str], indices: tuple = ()):
    """Yield (day, {symbol: (DayInput, 20-session average daily volume)},
    {index: 1-min bars}) for consecutive `days`. prior_days = the two store
    days before the first one (indicator warm-up); stocks = the symbols with
    a daily file (index CSVs have none)."""
    root = Path(store)
    stocks = {p.stem for p in (root / "daily").glob("*.csv")}
    hist: dict[str, DailyHistory] = {}
    cache: list[dict] = []                            # aggregates of the last two sessions

    def load(day: str) -> tuple[dict, dict]:
        raw = {p.stem: read_bars(p) for p in (root / day).glob("*.csv") if p.stem in stocks}
        return raw, {s: {m: aggregate(b, m) for m in TIMEFRAMES} for s, b in raw.items()}

    for pd in prior_days[-2:]:
        cache.append(load(pd)[1])
    for day in days:
        raw, aggs = load(day)
        idx = {n: read_bars(root / day / f"{n}.csv") if (root / day / f"{n}.csv").exists() else []
               for n in ("NIFTY", *indices)}
        inputs = {}
        for sym in sorted(raw):
            if not raw[sym]:
                continue
            info = hist.setdefault(sym, DailyHistory(root / "daily" / f"{sym}.csv")).before(day)
            avg_v = info.pop("avg_volume", None)
            prior = {m: [c for sess in cache if sym in sess for c in sess[sym][m]]
                     for m in TIMEFRAMES}
            inputs[sym] = (DayInput(raw[sym], aggs[sym], prior, idx["NIFTY"], **info), avg_v)
        yield day, inputs, idx
        cache = (cache + [aggs])[-2:]


def rvol5(bars: list[K], avg_volume: float | None, first5_share: float) -> float | None:
    """First-5-min volume vs normal (the 20-session average day x the
    market's usual first-5-min share)."""
    first5 = sum(b.v for b in bars if b.t < 5)
    return first5 / (avg_volume * first5_share) if avg_volume and first5_share else None


def run_chunk(store: str, days: list[str], prior_days: list[str], out_csv: str,
              costs_dict: dict, first5_share: float) -> dict:
    """Study consecutive `days` and write one CSV row per signal."""
    costs = CostModel.from_dict(costs_dict)
    counts = defaultdict(int)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for day, inputs, _ in iter_days(store, days, prior_days):
            for sym, (d, avg_v) in inputs.items():
                w.writerows(study_stock_day(day, sym, d, costs, rvol5(d.bars, avg_v, first5_share),
                                            counts))
                counts["stock_days"] += 1
    return dict(counts)


# -- summary -------------------------------------------------------------------------------

def metrics(nets: list[float], days: list[str], charges: list[float]) -> dict:
    n = len(nets)
    gp = sum(x for x in nets if x > 0)
    gl = -sum(x for x in nets if x < 0)
    by_day: dict[str, float] = defaultdict(float)
    for d, x in zip(days, nets):
        by_day[d] += x
    peak = cum = dd = 0.0
    for d in sorted(by_day):
        cum += by_day[d]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    net, ch = sum(nets), sum(charges)
    return {"trades": n, "win_rate": 100 * sum(x > 0 for x in nets) / n if n else 0.0,
            "net": round(net, 2), "charges": round(ch, 2), "gross": round(net + ch, 2),
            "avg_net": round(net / n, 2) if n else 0.0,
            "pf": gp / gl if gl else (float("inf") if gp else 0.0), "max_dd": round(dd, 2)}


def passes(m: dict) -> bool:
    return m["trades"] >= PASS_TRADES and m["net"] > 0 and m["pf"] >= PASS_PF


RVOL_BUCKETS = ((None, "n/a"), (1.0, "<1"), (2.0, "1-2"), (5.0, "2-5"), (float("inf"), ">=5"))


def rvol_bucket(x) -> str:
    if x in ("", None):
        return "n/a"
    x = float(x)
    return next(name for hi, name in RVOL_BUCKETS[1:] if x < hi)


def summarize(rows) -> dict:
    """(setup, side, exit, ALL | MTF | NOPATTERN) -> metrics, plus targets
    hit, net by regime at entry, and the before-cost P&L (gross) by the
    day's NIFTY type and by opening RVOL bucket (diagnostics, #31)."""
    acc = defaultdict(lambda: {"nets": [], "days": [], "charges": [], "targets": 0,
                               "regime": defaultdict(float),
                               "gross_day": defaultdict(lambda: [0, 0.0]),
                               "gross_rvol": defaultdict(lambda: [0, 0.0])})

    def add(key, r, e):
        a = acc[key]
        net, ch = float(r[f"{e}_net"]), float(r[f"{e}_charges"])
        a["nets"].append(net)
        a["days"].append(r["day"])
        a["charges"].append(ch)
        a["targets"] += r[f"{e}_reason"] == "TARGET"
        a["regime"][r["regime"]] += net
        for slot in (a["gross_day"][r["day_type"]], a["gross_rvol"][rvol_bucket(r["rvol5"])]):
            slot[0] += 1
            slot[1] += net + ch

    for r in rows:
        if str(r["candle"]) == "1":
            for e in EXITS:
                add((r["setup"], r["side"], e, "ALL"), r, e)
                if str(r["mtf"]) == "1":
                    add((r["setup"], r["side"], e, "MTF"), r, e)
        else:
            add((r["setup"], r["side"], "NATIVE", "NOPATTERN"), r, "NATIVE")
    out = {}
    for key, a in acc.items():
        m = metrics(a["nets"], a["days"], a["charges"])
        m["target_hit_pct"] = 100 * a["targets"] / m["trades"] if m["trades"] else 0.0
        m["net_by_regime"] = {k: round(v, 2) for k, v in a["regime"].items()}
        for name in ("gross_day", "gross_rvol"):
            m[f"avg_{name}"] = {k: (n, round(g / n, 2)) for k, (n, g) in a[name].items() if n}
        m["passes"] = passes(m) and key[3] != "NOPATTERN"
        out[key] = m
    return out


def read_rows(paths, start: str | None = None, stop: str | None = None):
    for p in paths:
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (start is None or r["day"] >= start) and (stop is None or r["day"] <= stop):
                    yield r


def split_chunks(days: list[str], n: int) -> list[list[str]]:
    n = max(1, min(n, len(days)))
    size = -(-len(days) // n)
    return [days[i:i + size] for i in range(0, len(days), size)]


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)
