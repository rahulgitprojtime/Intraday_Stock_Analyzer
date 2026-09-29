"""The user's trading plan on the whole market — DECISIONS #32. SIMULATION ONLY.

Signals, fills and charges are those of the #31 setup study
(src/research/setup_backtest.py): the five setups on every stock, both
sides, a confirmed candle pattern, market entry at the next 1-min open.
What changes is the money management: Rs 80,000 per trade from a Rs 4 lakh
budget, a 2% trailing stop, +3/4/5% targets, and a daily plan: up to 3
entries (5 on a strong market, none while the market is quiet), in-play
stocks first, one trade per stock. Never imports src/broker.
"""

from __future__ import annotations

import csv
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from math import sqrt

from src.paper.costs import CostModel
from src.research.setup_backtest import (SESSION_END, SETUPS, SIDES, SLIPPAGE_BPS, SQUARE_OFF,
                                         Exit, K, _buy, _sell, clock, detect, iter_days,
                                         last_close, metrics, pattern_name, regime, rvol5, settle)

BUDGET = 400_000.0
POSITION_VALUE = 80_000.0          # BUDGET / STRONG_CAP: all of a day's trades can be open together
TRAIL_PCT = 2.0
EXITS = {"T3": 3.0, "T4": 4.0, "T5": 5.0, "TRAIL": None}
FLAT_PCT, STRONG_PCT = 0.25, 0.5
QUIET_VOLUME, STRONG_VOLUME = 0.8, 1.0
BASE_CAP, STRONG_CAP = 3, 5
PORTFOLIOS = {"ALL": (SETUPS, True), **{s: ((s,), True) for s in SETUPS},
              "ALL_NOGATE": (SETUPS, False)}
PASS_TRADES, PASS_PF = 100, 1.1


def trail_exit(bars: list[K], i: int, entry: float, trail: float,
               target: float | None = None) -> Exit:
    """Walk the 1-min bars from the entry bar (long view). Per bar: the stop
    first (touch; a gap fills at the open), then the target (limit, trade-
    through only). After a bar closes without an exit, the stop trails to
    its high less `trail`, never down. Flat at 15:15."""
    first = stop = entry - trail
    high = last = entry
    for b in bars[i:]:
        if b.t >= SQUARE_OFF:
            break
        if b.l <= stop:
            px = min(b.o, stop)
            return Exit(b.t, _sell(px), "TRAIL" if stop > first else "STOP", px)
        if target is not None and b.h > target:
            px = round(max(b.o, target), 4)
            return Exit(b.t, px, "TARGET", px)
        last = b.c
        if b.h > high:
            high = b.h
            stop = max(stop, high - trail)
    return Exit(SQUARE_OFF, _sell(last), "SQUARE_OFF", last)


class MarketGate:
    """The market at minute T, from bars closed by T. QUIET = NIFTY and BANK
    NIFTY both flat and the market's volume thin: no new trades. STRONG = a
    clear index move on at least normal volume: up to 5 entries. Else NORMAL:
    up to 3. Volume comes from the stocks (index feeds carry none)."""

    def __init__(self, indices: list[list[K]], stocks: list[tuple[list[K], float | None]],
                 curve: list[float]) -> None:
        self._moves = []
        for bars in indices:
            if bars and bars[0].o:
                op = bars[0].o
                self._moves.append([None if x is None else abs(x / op - 1) * 100
                                    for x in last_close(bars)])
        per_min = [0.0] * (SESSION_END + 1)
        normal = 0.0
        for bars, avg in stocks:
            if avg and bars:
                normal += avg
                for b in bars:
                    per_min[b.t + 1] += b.v
        self.volume: list[float | None] = [None] * (SESSION_END + 1)
        cum = 0.0
        for T in range(1, SESSION_END + 1):
            cum += per_min[T]
            share = curve[T] if T < len(curve) else 1.0
            self.volume[T] = cum / (normal * share) if normal and share else None

    def move(self, T: int) -> float | None:
        now = [m[T] for m in self._moves if m[T] is not None]
        return max(now) if now else None

    def state(self, T: int) -> str:
        move, vol = self.move(T), self.volume[T]
        if move is None or vol is None:
            return "NORMAL"
        if move < FLAT_PCT and vol < QUIET_VOLUME:
            return "QUIET"
        if move >= STRONG_PCT and vol >= STRONG_VOLUME:
            return "STRONG"
        return "NORMAL"

    def cap(self, T: int, gate: bool = True) -> int:
        s = self.state(T)
        if s == "STRONG":
            return STRONG_CAP
        return 0 if s == "QUIET" and gate else BASE_CAP


@dataclass
class Candidate:
    day: str
    symbol: str
    setup: str
    side: str
    pattern: str
    t: int                          # minute the signal candle closed
    entry_t: int
    entry: float                    # real price (a short's selling price)
    qty: int
    rvol: float | None
    # exit -> (minute, real fill, reason, net, charges, gross before costs, slippage)
    exits: dict = field(default_factory=dict)
    candle: bool = True             # False: control signal found without the candle rule, never traded
    mtf: bool = False               # tag only: 10/30-min and daily trends agreed with the side
    regime: str = ""                # tag only: NIFTY's move since its open at entry


def candidates(day: str, symbol: str, d, costs: CostModel, rvol: float | None,
               nifty_move: list | None = None) -> list[Candidate]:
    """Every signal of the stock-day with each exit variant: the plan's
    candle-confirmed signals plus, for diagnostics only, the control signals
    found without the candle rule (candle=False; never selected)."""
    out = []
    for side in SIDES:
        v = d if side == "LONG" else d.mirrored()
        sgn = 1 if side == "LONG" else -1
        times = [b.t for b in v.bars]
        for candle in (True, False):
            for sig in detect(v, candle):
                i = bisect_left(times, sig.t)
                qty = int(POSITION_VALUE // abs(sig.ref))
                if i >= len(v.bars) or v.bars[i].t >= SQUARE_OFF or qty < 1:
                    continue
                opened, at = v.bars[i].o, v.bars[i].t
                fill = _buy(opened)
                trail = TRAIL_PCT / 100 * abs(fill)
                c = Candidate(day, symbol, sig.setup, side, pattern_name(side, sig.pattern) or "",
                              sig.t, at, sgn * fill, qty, rvol, candle=candle, mtf=sig.mtf,
                              regime=regime(nifty_move[at] if nifty_move else None))
                for name, pct in EXITS.items():
                    target = fill + abs(fill) * pct / 100 if pct else None
                    x = trail_exit(v.bars, i, fill, trail, target)
                    filled, ch, net = settle(side, qty, fill, x.price, costs)
                    gross = round((x.raw - opened) * qty, 4)          # at prices before slippage
                    c.exits[name] = (x.t, sgn * x.price, x.reason, net, ch, gross,
                                     round(gross - filled, 4))
                out.append(c)
    return out


def select(cands: list[Candidate], gate, setups, use_gate: bool = True) -> list[Candidate]:
    """The day's trades: candle-confirmed signals in time order (same minute:
    higher opening RVOL first, then symbol, then setup in SETUPS order), one
    trade per stock, the number of entries capped by the market state at
    each signal."""
    taken, traded = [], set()
    order = {s: k for k, s in enumerate(SETUPS)}
    for c in sorted(cands, key=lambda c: (c.t, -(c.rvol or 0.0), c.symbol, order[c.setup], c.side)):
        if not c.candle or c.setup not in setups or c.symbol in traded:
            continue
        if len(taken) >= gate.cap(c.t, use_gate):
            continue
        taken.append(c)
        traded.add(c.symbol)
    return taken


PLAN_COLUMNS = ["day", "symbol", "setup", "side", "pattern", "signal_time", "entry_time", "entry",
                "qty", "rvol5", "market", "candle", "mtf", "regime", "day_type", "avg_value_cr"] + \
    [f"{e}_{k}" for e in EXITS
     for k in ("time", "price", "reason", "net", "charges", "gross", "slippage", "hold")] + \
    [f"sel_{p}" for p in PORTFOLIOS]
DAY_COLUMNS = ["day", "state_0930", "index_move_0930", "market_volume_0930", "quiet_minutes",
               "strong_minutes", "signals"] + [f"n_{p}" for p in PORTFOLIOS]


def run_plan_chunk(store: str, days: list[str], prior_days: list[str], rows_csv: str,
                   days_csv: str, costs_dict: dict, curve: list[float]) -> dict:
    """Simulate consecutive `days`: one row per signal (with the portfolios
    that took it) and one row per day (market gate, trades taken)."""
    costs = CostModel.from_dict(costs_dict)
    counts = defaultdict(int)
    with open(rows_csv, "w", newline="", encoding="utf-8") as f, \
            open(days_csv, "w", newline="", encoding="utf-8") as g:
        rows = csv.DictWriter(f, fieldnames=PLAN_COLUMNS)
        per_day = csv.DictWriter(g, fieldnames=DAY_COLUMNS)
        rows.writeheader()
        per_day.writeheader()
        for day, inputs, idx in iter_days(store, days, prior_days, indices=("BANKNIFTY",)):
            if not inputs:                                # a store gap, not a skipped session
                counts["days_without_stock_data"] += 1
                continue
            gate = MarketGate([idx["NIFTY"], idx["BANKNIFTY"]],
                              [(d.bars, av) for d, av in inputs.values()], curve)
            nifty = idx["NIFTY"]
            n_open = nifty[0].o if nifty else None
            n_move = [None if x is None or not n_open else (x / n_open - 1) * 100
                      for x in last_close(nifty)]
            day_type = regime((nifty[-1].c / n_open - 1) * 100 if nifty and n_open else None)
            cands, value = [], {}
            for sym, (d, av) in inputs.items():
                cands += candidates(day, sym, d, costs, rvol5(d.bars, av, curve[5]), n_move)
                value[sym] = round(av * d.pdc / 1e7, 3) if av and d.pdc else ""
            confirmed = sum(c.candle for c in cands)
            counts["stock_days"] += len(inputs)
            counts["signals"] += confirmed
            counts["control_signals"] += len(cands) - confirmed
            picked = {p: {id(c) for c in select(cands, gate, setups, use_gate)}
                      for p, (setups, use_gate) in PORTFOLIOS.items()}
            for c in cands:
                row = {"day": c.day, "symbol": c.symbol, "setup": c.setup, "side": c.side,
                       "pattern": c.pattern, "signal_time": clock(c.t),
                       "entry_time": clock(c.entry_t), "entry": c.entry, "qty": c.qty,
                       "rvol5": "" if c.rvol is None else round(c.rvol, 3),
                       "market": gate.state(c.t), "candle": int(c.candle), "mtf": int(c.mtf),
                       "regime": c.regime, "day_type": day_type,
                       "avg_value_cr": value[c.symbol]}
                for name, (t, px, reason, net, ch, gross, slip) in c.exits.items():
                    row |= {f"{name}_time": clock(t), f"{name}_price": px,
                            f"{name}_reason": reason, f"{name}_net": net, f"{name}_charges": ch,
                            f"{name}_gross": gross, f"{name}_slippage": slip,
                            f"{name}_hold": t - c.entry_t}
                rows.writerow(row | {f"sel_{p}": int(id(c) in picked[p]) for p in PORTFOLIOS})
            move, vol = gate.move(15), gate.volume[15]
            window = range(15, 316)
            per_day.writerow({"day": day, "state_0930": gate.state(15),
                              "index_move_0930": "" if move is None else round(move, 3),
                              "market_volume_0930": "" if vol is None else round(vol, 3),
                              "quiet_minutes": sum(gate.state(T) == "QUIET" for T in window),
                              "strong_minutes": sum(gate.state(T) == "STRONG" for T in window),
                              "signals": confirmed,
                              **{f"n_{p}": len(picked[p]) for p in PORTFOLIOS}})
    return dict(counts)


# -- summary -------------------------------------------------------------------------------

def slippage(qty: int, entry: float, exit_px: float) -> float:
    """Rupees lost to the modelled slippage on both orders (real prices)."""
    return SLIPPAGE_BPS / 10_000 * qty * (abs(entry) + abs(exit_px))


class _Acc:
    __slots__ = ("n", "wins", "net", "charges", "raw", "gp", "gl")

    def __init__(self) -> None:
        self.n = self.wins = 0
        self.net = self.charges = self.raw = self.gp = self.gl = 0.0

    def add(self, net: float, ch: float, raw: float) -> None:
        self.n += 1
        self.wins += net > 0
        self.net += net
        self.charges += ch
        self.raw += raw
        if net > 0:
            self.gp += net
        else:
            self.gl -= net

    def result(self) -> dict:
        n = self.n
        return {"trades": n, "win_rate": 100 * self.wins / n if n else 0.0,
                "net": round(self.net, 2), "charges": round(self.charges, 2),
                "avg_net": round(self.net / n, 2) if n else 0.0,
                "avg_gross": round((self.net + self.charges) / n, 2) if n else 0.0,
                "avg_before_costs": round(self.raw / n, 2) if n else 0.0,
                "pf": self.gp / self.gl if self.gl else (float("inf") if self.gp else 0.0)}


def portfolio_metrics(nets: list[float], days_of: list[str], charges: list[float],
                      reasons: dict, raws: list[float], all_days: list[str]) -> dict:
    m = metrics(nets, days_of, charges)
    m["before_costs"] = round(sum(raws), 2)          # price moves only: no charges, no slippage
    by_day: dict[str, float] = defaultdict(float)
    for d, x in zip(days_of, nets):
        by_day[d] += x
    daily = [by_day.get(d, 0.0) for d in all_days]
    rets = [x / BUDGET for x in daily]
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    sd = sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    monthly: dict[str, float] = defaultdict(float)
    for d, x in by_day.items():
        monthly[d[:7]] += x
    total = sum(reasons.values())
    m |= {"days": n, "days_traded": len(by_day), "days_skipped": n - len(by_day),
          "return_pct": 100 * m["net"] / BUDGET, "max_dd_pct": 100 * m["max_dd"] / BUDGET,
          "sharpe": mean / sd * sqrt(252) if sd else 0.0,
          "best_day": max(daily, default=0.0), "worst_day": min(daily, default=0.0),
          "exit_pct": {k: 100 * v / total for k, v in reasons.items()} if total else {},
          "monthly": {k: round(v, 2) for k, v in sorted(monthly.items())}}
    m["passes"] = m["trades"] >= PASS_TRADES and m["net"] > 0 and m["pf"] >= PASS_PF
    return m


def summarize_plan(rows, all_days: list[str]) -> tuple[dict, dict]:
    """(portfolio, exit) -> portfolio metrics over `all_days`; and
    (setup, side, exit) -> per-trade metrics of every signal (no selection)."""
    port = {(p, e): ([], [], [], defaultdict(int), []) for p in PORTFOLIOS for e in EXITS}
    sig: dict[tuple, _Acc] = defaultdict(_Acc)
    for r in rows:
        if str(r.get("candle", "1")) != "1":             # no-candle controls: diagnostics only
            continue
        chosen = [p for p in PORTFOLIOS if str(r[f"sel_{p}"]) == "1"]
        qty, entry = int(float(r["qty"])), float(r["entry"])
        for e in EXITS:
            net, ch = float(r[f"{e}_net"]), float(r[f"{e}_charges"])
            exact = r.get(f"{e}_gross")                   # older runs: estimate the slippage
            raw = float(exact) if exact not in (None, "") else \
                net + ch + slippage(qty, entry, float(r[f"{e}_price"]))
            sig[(r["setup"], r["side"], e)].add(net, ch, raw)
            for p in chosen:
                nets, ds, chs, reasons, raws = port[(p, e)]
                nets.append(net)
                ds.append(r["day"])
                chs.append(ch)
                reasons[r[f"{e}_reason"]] += 1
                raws.append(raw)
    return ({k: portfolio_metrics(*v, all_days) for k, v in port.items()},
            {k: a.result() for k, a in sig.items()})
