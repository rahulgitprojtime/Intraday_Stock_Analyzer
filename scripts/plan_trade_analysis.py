"""Trade-level breakdown of a plan-study run (DECISIONS #32). Read-only.

    python scripts/plan_trade_analysis.py --run data/research/plan_study/explore_tagged \
        --out docs/research/plan_trade_analysis.md
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.research import setup_backtest as sb  # noqa: E402
from src.research import trade_analysis as ta  # noqa: E402

ORDER = {
    "setup": list(sb.SETUPS), "side": ["LONG", "SHORT"],
    "exit reason": ["TARGET", "TRAIL", "STOP", "SQUARE_OFF"],
    "NIFTY at entry": ["UP", "SIDEWAYS", "DOWN", "UNKNOWN"],
    "NIFTY day (hindsight)": ["UP", "SIDEWAYS", "DOWN", "UNKNOWN"],
    "market gate at signal": ["QUIET", "NORMAL", "STRONG"],
    "day of week": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "holding time": ["0-4 min", "5-14 min", "15-29 min", "30-59 min", "60-119 min", "120-239 min",
                     "240+ min"],
    "stock price": ["<250", "250-500", "500-1000", "1000-2500", ">=2500"],
    "stock liquidity": ["<10 cr", "10-50 cr", "50-200 cr", "200-1000 cr", ">=1000 cr", "n/a"],
    "opening RVOL": ["<1", "1-2", "2-5", "5-10", ">=10", "n/a"],
}
HEAD = ("| {first} | trades | gross Rs | costs Rs | net Rs | avg gross Rs | avg net Rs | win % | PF "
        "| max DD Rs | t (gross) | avg gross Nov-Feb | avg gross Mar-Jun |")
RULE = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"


def _num(x, d=0):
    return "-" if x is None else f"{x:,.{d}f}"


def row(label: str, m: dict) -> str:
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return (f"| {label} | {m['trades']:,} | {_num(m['gross'])} | {_num(m['costs'])} | "
            f"{_num(m['net'])} | {_num(m['avg_gross'], 1)} | {_num(m['avg_net'], 1)} | "
            f"{m['win_rate']:.1f} | {pf} | {_num(m['max_dd'])} | {m['t_gross']:.1f} | "
            f"{_num(m['avg_gross_h1'], 1)} | {_num(m['avg_gross_h2'], 1)} |")


def labels_of(res: dict, pop: str, e: str, dim: str) -> list[str]:
    found = {k[3] for k in res if k[:3] == (pop, e, dim)}
    first = [x for x in ORDER.get(dim, []) if x in found]
    return first + sorted(found - set(first))


def table(res: dict, pop: str, e: str, dim: str, title: str) -> list[str]:
    out = [f"**{title}**", "", HEAD.format(first=dim), RULE]
    out += [row(lab, res[(pop, e, dim, lab)]) for lab in labels_of(res, pop, e, dim)]
    return out + [""]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", type=Path, required=True, help="plan_study output folder")
    p.add_argument("--out", type=Path, required=True, help="markdown report")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="stop")
    a = p.parse_args(argv)

    buckets = ta.breakdown(sb.read_rows(sorted(a.run.glob("rows_*.csv")), a.start, a.stop))
    res = {k: b.result(ta.SPLIT) for k, b in buckets.items()}
    e = ta.MAIN_EXIT
    pool, plan, ctrl = res[("POOL", e, "total", "all")], res[("PLAN", e, "total", "all")], \
        res[("CONTROL", e, "total", "all")]

    L = ["# Trade-level analysis: the Rs 4 lakh plan, Nov 2025 - Jun 2026 (DECISIONS #32)", "",
         "Rules unchanged; this only re-reads the backtest's trades. Gross = price move x quantity "
         "before any cost. Costs = Groww charges + slippage (5 bps on market and stop orders, "
         "none on target limit orders). Net = gross - costs. Win rate and PF use net. Max DD = "
         "largest fall of cumulative net at day ends. t = average gross / its day-clustered "
         "standard error: |t| below ~2 is indistinguishable from zero. Last two columns: average "
         "gross in each half of the period (a pattern that flips sign is not stable).", "",
         f"Populations: POOL = all {pool['trades']:,} candle-confirmed signals, each as if "
         f"traded with Rs 80,000 (the pool the plan picks from); PLAN = the {plan['trades']:,} "
         "trades the all-setups plan actually took; CONTROL = the first signal of each setup "
         f"found without the candle rule ({ctrl['trades']:,}). Entry-side tables use the "
         f"{e} exit (+4% target, the middle of the 3-5% range); the exit section shows all four.",
         "", "## 1. Totals and where the costs go", "",
         HEAD.format(first="population"), RULE,
         row("POOL (all signals)", pool), row("PLAN (trades taken)", plan),
         row("CONTROL (no candle rule)", ctrl), ""]
    for name, m in (("POOL", pool), ("PLAN", plan)):
        n = m["trades"] or 1
        L.append(f"- {name}: charges Rs {m['charges'] / n:,.1f} + slippage Rs "
                 f"{m['slippage'] / n:,.1f} = Rs {m['costs'] / n:,.1f} per trade; average "
                 f"gross Rs {m['avg_gross']:,.1f} per trade.")
    L += ["", "## 2. Setup", ""]
    L += table(res, "POOL", e, "setup", "POOL") + table(res, "PLAN", e, "setup", "PLAN")
    L += ["## 3. Long vs short", ""]
    L += table(res, "POOL", e, "side", "POOL") + table(res, "PLAN", e, "side", "PLAN")
    L += table(res, "POOL", e, "setup x side", "POOL, setup x side")
    L += ["## 4. Target / exit type", "", HEAD.format(first="exit variant"), RULE]
    for pop in ("POOL", "PLAN"):
        L += [row(f"{pop} {x}", res[(pop, x, "total", "all")]) for x in ta.EXITS]
    L.append("")
    for x in ta.EXITS:
        L += table(res, "POOL", x, "exit reason", f"POOL, exit reason with {x}")
    for x in ta.EXITS:
        L += table(res, "PLAN", x, "exit reason", f"PLAN, exit reason with {x}")
    L += ["## 5. Candle confirmation", "",
          "CONTROL signals are found without the candle rule; the split is whether the signal "
          "candle happened to form a pattern. POOL (candle required) is shown for reference.", ""]
    L += table(res, "CONTROL", e, "candle confirmation", "CONTROL: pattern present vs none")
    L += table(res, "CONTROL", e, "setup x candle", "CONTROL, setup x candle")
    L += table(res, "CONTROL", e, "side x candle", "CONTROL, side x candle")
    L += table(res, "POOL", e, "candle pattern", "POOL, by candle pattern")
    L += ["## 6. Trend filter (10-min, 30-min and daily trends agree with the side)", ""]
    L += table(res, "POOL", e, "trend filter", "POOL") + table(res, "PLAN", e, "trend filter", "PLAN")
    L += table(res, "POOL", e, "side x trend filter", "POOL, side x trend filter")
    L += ["## 7. NIFTY regime", "",
          "At entry = NIFTY's move since its open when the trade is entered (known then): UP "
          "> +0.25%, DOWN < -0.25%, else SIDEWAYS. Day (hindsight) = NIFTY's close vs open; "
          "not knowable at entry, shown only to locate the edge.", ""]
    for dim in ("NIFTY at entry", "side x NIFTY at entry", "NIFTY day (hindsight)",
                "side x NIFTY day (hindsight)", "market gate at signal"):
        L += table(res, "POOL", e, dim, f"POOL, {dim}")
    L += table(res, "PLAN", e, "NIFTY at entry", "PLAN, NIFTY at entry")
    L += table(res, "PLAN", e, "side x NIFTY at entry", "PLAN, side x NIFTY at entry")
    L += ["## 8. Time of entry", ""]
    L += table(res, "POOL", e, "entry time", "POOL") + table(res, "PLAN", e, "entry time", "PLAN")
    L += ["## 9. Day of week", "",
          "Sun = the special Union Budget session on 1 Feb 2026.", ""]
    L += table(res, "POOL", e, "day of week", "POOL") + table(res, "PLAN", e, "day of week", "PLAN")
    L += ["## 10. Holding time (entry to exit, with the +4% target)", ""]
    L += table(res, "POOL", e, "holding time", "POOL") + table(res, "PLAN", e, "holding time", "PLAN")
    L += ["## 11. Stock", ""]
    for dim in ("stock price", "stock liquidity", "opening RVOL"):
        L += table(res, "POOL", e, dim, f"POOL, {dim}") + table(res, "PLAN", e, dim, f"PLAN, {dim}")
    stocks = {k[3]: m for k, m in res.items() if k[:3] == ("POOL", e, "stock")}
    stock_csv = a.run / "by_stock.csv"
    with stock_csv.open("w", newline="", encoding="utf-8") as f:
        cols = ["trades", "gross", "costs", "net", "avg_gross", "avg_net", "win_rate", "pf",
                "max_dd", "t_gross", "avg_gross_h1", "avg_gross_h2"]
        w = csv.writer(f)
        w.writerow(["symbol"] + cols)
        for s in sorted(stocks):
            w.writerow([s] + [stocks[s][c] if not isinstance(stocks[s][c], float)
                              else round(stocks[s][c], 2) for c in cols])
    both = [(m["avg_gross_h1"], m["avg_gross_h2"]) for m in stocks.values()
            if m["avg_gross_h1"] is not None and m["avg_gross_h2"] is not None and m["trades"] >= 60]
    rho = ta.spearman([x for x, _ in both], [y for _, y in both])
    vals = sorted(m["avg_gross"] for m in stocks.values())
    q = lambda f: vals[min(len(vals) - 1, int(f * len(vals)))]  # noqa: E731
    shown = stock_csv.as_posix()
    shown = shown[shown.find("data/research"):] if "data/research" in shown else shown
    L += [f"**Per stock (POOL, {e})** — {len(stocks)} stocks; full table (alphabetical, not "
          f"committed: market data): `{shown}`.", "",
          f"- Stocks with average gross > 0: {sum(m['avg_gross'] > 0 for m in stocks.values())}; "
          f"with average net > 0: {sum(m['avg_net'] > 0 for m in stocks.values())}.",
          f"- Average gross per trade across stocks: 10th pct Rs {q(0.1):,.0f}, median Rs "
          f"{q(0.5):,.0f}, 90th pct Rs {q(0.9):,.0f}.",
          f"- Persistence: rank correlation of a stock's average gross in Nov-Feb vs Mar-Jun "
          f"= {rho:.2f} ({len(both)} stocks with >= 60 signals; 0 = no persistence).", ""]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {a.out} and {stock_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
