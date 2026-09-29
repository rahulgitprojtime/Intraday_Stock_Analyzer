"""Trade-level breakdown of a #32 plan-study run. Read-only analysis.

Every figure comes from the rows written by plan_backtest.run_plan_chunk
(no rule is re-applied or changed). gross = the price move x quantity before
any cost; costs = Groww charges + modelled slippage; net = gross - costs.
Win rate and profit factor use net. Max drawdown = the largest peak-to-trough
fall of cumulative net at day ends. t = average gross / its day-clustered
standard error (trades on one day share the market's move).

Populations:
- POOL: every candle-confirmed signal, each as if traded (Rs 80k) — the pool
  the plan picks from.
- CONTROL: the first signal of each setup found WITHOUT the candle rule,
  split by whether its candle happened to form a pattern.
- PLAN: the trades the Rs 4 lakh all-setups plan actually took (sel_ALL).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import sqrt

EXITS = ("T3", "T4", "T5", "TRAIL")
MAIN_EXIT = "T4"                     # middle of the user's 3-5% target range
SPLIT = "2026-03-01"                 # halves: Nov-Feb vs Mar-Jun


class Bucket:
    __slots__ = ("n", "gross", "charges", "slip", "net", "wins", "gp", "gl",
                 "day_n", "day_gross", "day_net")

    def __init__(self) -> None:
        self.n = self.wins = 0
        self.gross = self.charges = self.slip = self.net = self.gp = self.gl = 0.0
        self.day_n: dict = defaultdict(int)
        self.day_gross: dict = defaultdict(float)
        self.day_net: dict = defaultdict(float)

    def add(self, day: str, gross: float, charges: float, slip: float, net: float) -> None:
        self.n += 1
        self.gross += gross
        self.charges += charges
        self.slip += slip
        self.net += net
        if net > 0:
            self.wins += 1
            self.gp += net
        else:
            self.gl -= net
        self.day_n[day] += 1
        self.day_gross[day] += gross
        self.day_net[day] += net

    def result(self, split: str | None = None) -> dict:
        n = self.n
        peak = cum = dd = 0.0
        for d in sorted(self.day_net):
            cum += self.day_net[d]
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        mean = self.gross / n if n else 0.0
        days = len(self.day_n)
        var = sum((self.day_gross[d] - self.day_n[d] * mean) ** 2 for d in self.day_n)
        se = sqrt(var * days / (days - 1)) / n if n and days > 1 else 0.0
        out = {"trades": n, "gross": self.gross, "charges": self.charges, "slippage": self.slip,
               "costs": self.charges + self.slip, "net": self.net,
               "avg_gross": mean, "avg_net": self.net / n if n else 0.0,
               "win_rate": 100 * self.wins / n if n else 0.0,
               "pf": self.gp / self.gl if self.gl else (float("inf") if self.gp else 0.0),
               "max_dd": dd, "t_gross": mean / se if se else 0.0}
        if split:
            for name, keep in (("h1", lambda d: d < split), ("h2", lambda d: d >= split)):
                k = sum(v for d, v in self.day_n.items() if keep(d))
                g = sum(v for d, v in self.day_gross.items() if keep(d))
                out[f"avg_gross_{name}"] = g / k if k else None
        return out


# -- labels --------------------------------------------------------------------------------

def _band(x: float, edges: tuple, labels: tuple) -> str:
    return next((lab for edge, lab in zip(edges, labels) if x < edge), labels[-1])


def entry_band(hhmm: str) -> str:
    return _band(int(hhmm[:2]) * 60 + int(hhmm[3:5]),
                 (570, 600, 630, 660, 720, 780, 840),
                 ("09:25-09:29", "09:30-09:59", "10:00-10:29", "10:30-10:59", "11:00-11:59",
                  "12:00-12:59", "13:00-13:59", "14:00-14:30"))


def hold_band(minutes: int) -> str:
    return _band(minutes, (5, 15, 30, 60, 120, 240),
                 ("0-4 min", "5-14 min", "15-29 min", "30-59 min", "60-119 min", "120-239 min",
                  "240+ min"))


def weekday(day: str) -> str:
    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[date.fromisoformat(day).weekday()]


def rvol_band(x: str) -> str:
    if x in ("", None):
        return "n/a"
    return _band(float(x), (1, 2, 5, 10), ("<1", "1-2", "2-5", "5-10", ">=10"))


def price_band(p: float) -> str:
    return _band(abs(p), (250, 500, 1000, 2500), ("<250", "250-500", "500-1000", "1000-2500", ">=2500"))


def value_band(x: str) -> str:
    if x in ("", None):
        return "n/a"
    return _band(float(x), (10, 50, 200, 1000),
                 ("<10 cr", "10-50 cr", "50-200 cr", "200-1000 cr", ">=1000 cr"))


ENTRY_DIMS = {
    "setup": lambda r: r["setup"],
    "side": lambda r: r["side"],
    "setup x side": lambda r: f"{r['setup']} {r['side']}",
    "trend filter": lambda r: "10/30-min + daily agree" if r["mtf"] == "1" else "do not agree",
    "side x trend filter": lambda r: f"{r['side']} {'agree' if r['mtf'] == '1' else 'do not agree'}",
    "NIFTY at entry": lambda r: r["regime"],
    "side x NIFTY at entry": lambda r: f"{r['side']} {r['regime']}",
    "NIFTY day (hindsight)": lambda r: r["day_type"],
    "side x NIFTY day (hindsight)": lambda r: f"{r['side']} {r['day_type']}",
    "market gate at signal": lambda r: r["market"],
    "entry time": lambda r: entry_band(r["entry_time"]),
    "day of week": lambda r: weekday(r["day"]),
    "stock price": lambda r: price_band(float(r["entry"])),
    "stock liquidity": lambda r: value_band(r["avg_value_cr"]),
    "opening RVOL": lambda r: rvol_band(r["rvol5"]),
    "candle pattern": lambda r: r["pattern"] or "none",
}
EXIT_DIMS = {
    "exit reason": lambda r, e: r[f"{e}_reason"],
    "holding time": lambda r, e: hold_band(int(float(r[f"{e}_hold"]))),
}


def breakdown(rows) -> dict:
    """(population, exit, dimension, label) -> Bucket, in one pass."""
    out: dict = defaultdict(Bucket)

    def add(pop, e, dim, label, day, v):
        out[(pop, e, dim, label)].add(day, *v)

    for r in rows:
        day = r["day"]
        vals = {e: (float(r[f"{e}_gross"]), float(r[f"{e}_charges"]), float(r[f"{e}_slippage"]),
                    float(r[f"{e}_net"])) for e in EXITS}
        pops = []
        if r["candle"] == "1":
            pops.append("POOL")
            if str(r.get("sel_ALL")) == "1":
                pops.append("PLAN")
        else:
            confirmed = "candle pattern present" if r["pattern"] else "no pattern"
            v = vals[MAIN_EXIT]
            add("CONTROL", MAIN_EXIT, "total", "all", day, v)
            add("CONTROL", MAIN_EXIT, "candle confirmation", confirmed, day, v)
            add("CONTROL", MAIN_EXIT, "setup x candle", f"{r['setup']} {confirmed}", day, v)
            add("CONTROL", MAIN_EXIT, "side x candle", f"{r['side']} {confirmed}", day, v)
        for pop in pops:
            for e in EXITS:
                add(pop, e, "total", "all", day, vals[e])
                for dim, fn in EXIT_DIMS.items():
                    add(pop, e, dim, fn(r, e), day, vals[e])
            v = vals[MAIN_EXIT]
            for dim, fn in ENTRY_DIMS.items():
                add(pop, MAIN_EXIT, dim, fn(r), day, v)
            if pop == "POOL":
                add(pop, MAIN_EXIT, "stock", r["symbol"], day, v)
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation (average ranks for ties)."""
    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    n = len(xs)
    if n < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / sqrt(vx * vy) if vx and vy else None
