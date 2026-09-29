"""Whole-market setup study, long and short (DECISIONS #31). SIMULATION ONLY.

Runs every stock in the store through the five setups x two sides x five
exits in parallel, writes one CSV row per signal and a markdown report.

    python scripts/setup_study.py --from 2025-11-03 --to 2026-06-30 --name explore
    python scripts/setup_study.py --from 2026-07-01 --to 2026-08-31 --name validate \
        --only-passed data/research/setup_study/explore/summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time as clock
from concurrent.futures import ProcessPoolExecutor
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.quantitative.volume_scan import expected_fraction, load_market_curve  # noqa: E402
from src.research import setup_backtest as sb  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


def key_str(k: tuple) -> str:
    return "|".join(k)


def report(summary: dict, title: str, counts: dict, only: set | None) -> str:
    keys = [k for k in summary if only is None or key_str(k) in only]
    keys.sort(key=lambda k: -summary[k]["net"])
    lines = [f"# {title}", "",
             f"Stock-days {counts.get('stock_days', 0):,}, signals {counts.get('signals', 0):,}, "
             f"skipped {counts.get('skipped', 0):,} (stop beyond the fill / no bar / price > Rs 25k). "
             "Rs 25,000 per trade, Groww charges, 5 bps slippage (DECISIONS #31).", "",
             "| setup | side | exit | filter | trades | win % | net Rs | avg Rs | PF | max DD Rs "
             "| charges Rs | target hit % | net UP | net DOWN | net SIDEWAYS | pass |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for k in keys:
        m, rg = summary[k], summary[k]["net_by_regime"]
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        lines.append(
            f"| {' | '.join(k)} | {m['trades']:,} | {m['win_rate']:.1f} | {m['net']:,.0f} | "
            f"{m['avg_net']:,.1f} | {pf} | {m['max_dd']:,.0f} | {m['charges']:,.0f} | "
            f"{m['target_hit_pct']:.1f} | {rg.get('UP', 0):,.0f} | {rg.get('DOWN', 0):,.0f} | "
            f"{rg.get('SIDEWAYS', 0):,.0f} | {'PASS' if m['passes'] else ''} |")
    cols = [("gross_day", d) for d in ("UP", "DOWN", "SIDEWAYS")] + \
        [("gross_rvol", b) for _, b in sb.RVOL_BUCKETS[1:]]
    lines += ["", "## Before-cost P&L per trade (Rs): candle entries, NATIVE exit, all stocks",
              "", "Gross = before charges (slippage included). Day type = the day's NIFTY close "
              "vs open (hindsight, a label only). RVOL = first-5-min volume vs normal "
              "(in-play check). Cells: average Rs (trades).", "",
              "| setup | side | all | UP days | DOWN days | SIDEWAYS days | RVOL <1 | 1-2 | 2-5 | >=5 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k in sorted(k for k in summary if k[2] == "NATIVE" and k[3] == "ALL"):
        m = summary[k]
        cells = []
        for name, b in cols:
            n, g = m[f"avg_{name}"].get(b, (0, 0.0))
            cells.append(f"{g:,.1f} ({n:,})" if n else "-")
        lines.append(f"| {k[0]} | {k[1]} | {m['gross'] / m['trades']:,.1f} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", type=Path, default=ROOT / "data" / "universe_1y")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="stop", required=True)
    p.add_argument("--name", required=True, help="output folder under --out-root")
    p.add_argument("--out-root", type=Path, default=ROOT / "data" / "research" / "setup_study")
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
        share = expected_fraction(load_market_curve(), time(9, 20))
        costs = load_yaml("costs.yaml")
        chunks = sb.split_chunks(days, a.workers)
        jobs = []
        for n, chunk in enumerate(chunks):
            k = all_days.index(chunk[0])
            jobs.append((str(a.store), chunk, all_days[max(0, k - 2):k],
                         str(out / f"rows_{n:02d}.csv"), costs, share))
        t0 = clock.time()
        print(f"{len(days)} days in {len(chunks)} chunks on {a.workers} workers ...", flush=True)
        total: dict = {}
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for c in ex.map(sb.run_chunk, *zip(*jobs)):
                for key, v in c.items():
                    total[key] = total.get(key, 0) + v
        counts_file.write_text(json.dumps(total), encoding="utf-8")
        print(f"done in {clock.time() - t0:,.0f} s: {total}", flush=True)
    counts = json.loads(counts_file.read_text(encoding="utf-8")) if counts_file.exists() else {}
    summary = sb.summarize(sb.read_rows(sorted(out.glob("rows_*.csv")), a.start, a.stop))
    only = None
    if a.only_passed:
        prev = json.loads(a.only_passed.read_text(encoding="utf-8"))
        only = {k for k, m in prev.items() if m["passes"]}
    (out / "summary.json").write_text(
        json.dumps({key_str(k): m for k, m in summary.items()}, indent=1, default=str),
        encoding="utf-8")
    title = f"Setup study {a.name}: {a.start}..{a.stop}"
    text = report(summary, title, counts, only)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
