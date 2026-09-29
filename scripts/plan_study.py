"""The user's trading plan on the whole market (DECISIONS #32). SIMULATION ONLY.

Rs 4 lakh budget, Rs 80,000 per trade, 2% trailing stop, +3/4/5% targets,
up to 3 trades a day (5 on a strong market, none while it is quiet), every
stock, both sides. Writes rows/day CSVs and a markdown report.

    python scripts/plan_study.py --from 2025-11-03 --to 2026-06-30 --name explore
    python scripts/plan_study.py --from 2026-07-01 --to 2026-08-31 --name validate \
        --only-passed data/research/plan_study/explore/summary.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time as clock
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.quantitative.volume_scan import load_market_curve  # noqa: E402
from src.research import plan_backtest as pb  # noqa: E402
from src.research import setup_backtest as sb  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


def _pf(x: float) -> str:
    return "inf" if x == float("inf") else f"{x:.2f}"


def report(title: str, counts: dict, ports: dict, signals: dict, day_rows: list[dict],
           only: set | None) -> str:
    keys = [k for k in ports if only is None or "|".join(k) in only]
    keys.sort(key=lambda k: -ports[k]["net"])
    n_days = len(day_rows)
    lines = [f"# {title}", "",
             f"{n_days} sessions, {counts.get('stock_days', 0):,} stock-days, "
             f"{counts.get('signals', 0):,} candle-confirmed signals (every stock, both sides). "
             "Rs 4 lakh budget, Rs 80,000 per trade, 2% trailing stop, targets +3/+4/+5% "
             "(TRAIL = no target), up to 3 entries a day (5 when STRONG, none while QUIET), "
             "one trade per stock, Groww charges, 5 bps slippage (DECISIONS #32). "
             f"Store days without stock data (left out): {counts.get('days_without_stock_data', 0)}.",
             "",
             "## Portfolios: what a Rs 4 lakh account would have done", "",
             "Before costs = the price moves alone (no charges, no slippage).", "",
             "| portfolio | exit | trades | days traded | days skipped | win % | before costs Rs "
             "| charges Rs | net Rs | return % | max DD Rs | PF | Sharpe | target % | trail % "
             "| stop % | 15:15 % | pass |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for k in keys:
        m, ex = ports[k], ports[k]["exit_pct"]
        lines.append(
            f"| {k[0]} | {k[1]} | {m['trades']:,} | {m['days_traded']} | {m['days_skipped']} | "
            f"{m['win_rate']:.1f} | {m['before_costs']:,.0f} | {m['charges']:,.0f} | "
            f"{m['net']:,.0f} | {m['return_pct']:.1f} | {m['max_dd']:,.0f} | "
            f"{_pf(m['pf'])} | {m['sharpe']:.2f} | {ex.get('TARGET', 0):.0f} | "
            f"{ex.get('TRAIL', 0):.0f} | {ex.get('STOP', 0):.0f} | {ex.get('SQUARE_OFF', 0):.0f} | "
            f"{'PASS' if m['passes'] else ''} |")
    at930 = Counter(r["state_0930"] for r in day_rows)
    strong = sum(int(r["strong_minutes"]) > 0 for r in day_rows)
    quiet_all = sum(int(r["quiet_minutes"]) == 301 for r in day_rows)
    no_trade = sum(r["n_ALL"] == "0" for r in day_rows)
    lines += ["", "## Market gate (NIFTY + BANK NIFTY move, whole-market volume)", "",
              f"- Market at 09:30: QUIET {at930.get('QUIET', 0)}, NORMAL {at930.get('NORMAL', 0)}, "
              f"STRONG {at930.get('STRONG', 0)} days.",
              f"- Days with a STRONG phase (5 entries allowed): {strong}. Days quiet the whole "
              f"session: {quiet_all}. Days the all-setups plan took no trade: {no_trade}.", ""]
    months = sorted({mo for e in pb.EXITS for mo in ports[("ALL", e)]["monthly"]})
    lines += ["## Monthly net, all setups (Rs)", "",
              "| month | " + " | ".join(pb.EXITS) + " |", "|---|" + "---:|" * len(pb.EXITS)]
    for mo in months:
        lines.append(f"| {mo} | " + " | ".join(
            f"{ports[('ALL', e)]['monthly'].get(mo, 0):,.0f}" for e in pb.EXITS) + " |")
    lines += ["", "## Every signal, no selection: per-trade result at Rs 80,000", "",
              "Shows whether the entries have an edge with these exits, independent of which "
              "3-5 trades a day are picked. Costs per trade: ~Rs 75 Groww charges + ~Rs 80 "
              "slippage.", "",
              "| setup | side | exit | trades | win % | avg before costs Rs | avg net Rs | PF |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
    order = {s: i for i, s in enumerate(sb.SETUPS)}
    for k in sorted(signals, key=lambda k: (order[k[0]], k[1], list(pb.EXITS).index(k[2]))):
        m = signals[k]
        lines.append(f"| {k[0]} | {k[1]} | {k[2]} | {m['trades']:,} | {m['win_rate']:.1f} | "
                     f"{m['avg_before_costs']:,.1f} | {m['avg_net']:,.1f} | {_pf(m['pf'])} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", type=Path, default=ROOT / "data" / "universe_1y")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="stop", required=True)
    p.add_argument("--name", required=True, help="output folder under --out-root")
    p.add_argument("--out-root", type=Path, default=ROOT / "data" / "research" / "plan_study")
    p.add_argument("--workers", type=int, default=sb.default_workers())
    p.add_argument("--report-only", action="store_true", help="re-summarise existing rows")
    p.add_argument("--only-passed", type=Path, help="report only variants that passed there")
    a = p.parse_args(argv)

    out = a.out_root / a.name
    out.mkdir(parents=True, exist_ok=True)
    all_days = sb.store_days(a.store)
    days = [d for d in all_days if a.start <= d <= a.stop]
    counts_file = out / "counts.json"
    if not a.report_only:
        if not days:
            print(f"no store days in {a.start}..{a.stop}")
            return 1
        curve, costs = load_market_curve(), load_yaml("costs.yaml")
        jobs = []
        for n, chunk in enumerate(sb.split_chunks(days, a.workers)):
            k = all_days.index(chunk[0])
            jobs.append((str(a.store), chunk, all_days[max(0, k - 2):k],
                         str(out / f"rows_{n:02d}.csv"), str(out / f"days_{n:02d}.csv"),
                         costs, curve))
        t0 = clock.time()
        print(f"{len(days)} days in {len(jobs)} chunks on {a.workers} workers ...", flush=True)
        total: dict = {}
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for c in ex.map(pb.run_plan_chunk, *zip(*jobs)):
                for key, v in c.items():
                    total[key] = total.get(key, 0) + v
        counts_file.write_text(json.dumps(total), encoding="utf-8")
        print(f"done in {clock.time() - t0:,.0f} s: {total}", flush=True)
    counts = json.loads(counts_file.read_text(encoding="utf-8")) if counts_file.exists() else {}
    day_rows = [r for path in sorted(out.glob("days_*.csv"))
                for r in csv.DictReader(path.open(encoding="utf-8")) if a.start <= r["day"] <= a.stop]
    ports, signals = pb.summarize_plan(sb.read_rows(sorted(out.glob("rows_*.csv")), a.start, a.stop),
                                       [r["day"] for r in day_rows])
    only = None
    if a.only_passed:
        prev = json.loads(a.only_passed.read_text(encoding="utf-8"))
        only = {k for k, m in prev.items() if m["passes"]}
    (out / "summary.json").write_text(
        json.dumps({"|".join(k): m for k, m in ports.items()}, indent=1, default=str),
        encoding="utf-8")
    text = report(f"Trading plan study {a.name}: {a.start}..{a.stop}", counts, ports, signals,
                  day_rows, only)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
