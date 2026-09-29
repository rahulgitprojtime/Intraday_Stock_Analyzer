"""Run the three pre-registered validation tests of DECISIONS #27 — M17.

    python scripts/validate_m17.py --out reports/validate_m17.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.research.evaluate import (  # noqa: E402
    HORIZONS, load_rows, summary, table, verdict)
from src.research.experiments import paired_ic, reblend  # noqa: E402

T1 = {"setup": 0.50, "volume": 0.10, "movement": 0.075, "momentum": 0.075,
      "sector": 0.10, "market": 0.10, "liquidity": 0.05}
PULLBACKS = ("EMA_PULLBACK", "GAP_AND_GO")


def fmt(x, nd=3):
    return "—" if x is None else f"{x:+.{nd}f}"


def t1(rows) -> list[str]:
    out = ["| horizon | IC m15 | IC T1 | T1 − m15 | paired t | days |", "|---|---|---|---|---|---|"]
    passes, ics = 0, {}
    for mode in ("DAY", "SCALP"):
        mrows = [r for r in rows if r["mode"] == mode]
        for h in HORIZONS:
            d, t, n, ia, ib = paired_ic(mrows, lambda r: reblend(r, T1), lambda r: r.get("base_score"),
                                        f"xs_nifty_{h}")
            out.append(f"| {mode} {h}m | {fmt(ib)} | {fmt(ia)} | {fmt(d)} | {fmt(t, 2)} | {n} |")
            if mode == "DAY":
                passes += d is not None and d > 0 and t is not None and t > 2
                ics[h] = ia
    ok = passes >= 2 and all(ics[h] is not None and ics[h] >= 0 for h in (30, 60))
    out.append(f"\n- DAY horizons with T1 better (t > 2): {passes}; T1 IC at 30/60m: "
               f"{fmt(ics[30])} / {fmt(ics[60])}")
    out.append(f"- **T1 verdict: {'PASS' if ok else 'FAIL'}**")
    return out


def t2(rows) -> list[str]:
    day = [r for r in rows if r["mode"] == "DAY" and (r.get("base_score") or 0) >= 50]
    lunch = [r for r in day if r.get("lunch_penalty")]
    other = [r for r in day if not r.get("lunch_penalty") and "11:00" <= r["as_of"][11:16] < "14:30"]
    v = verdict(lunch, other)
    return table({"lunch 11:30-13:30": lunch, "11:00-11:30 + 13:30-14:30": other}) + [
        f"\n- Verdict lunch vs neighbouring hours: {v}",
        f"- **T2 verdict: {'KEEP penalty' if v == 'FINDING: worse' else 'REMOVE penalty'}**"]


def t3(rows) -> list[str]:
    day = [r for r in rows if r["mode"] == "DAY"]
    pull = [r for r in day if any(r.get(f"setup_{s}") == "TRIGGERED" for s in PULLBACKS)]
    rest = [r for r in day if not any(r.get(f"setup_{s}") == "TRIGGERED" for s in PULLBACKS)]
    v = verdict(pull, rest)
    lines = table({"pullback list": pull, "all other DAY rows": rest}) + ["", "After the 0.1% cost (raw return, not excess):", ""]
    lines += table({"pullback list": pull}, metric="net")
    tradeable = False
    for h in (30, 60):
        s = summary(pull, f"net_{h}")
        tradeable |= s["lo"] is not None and s["lo"] > 0
    lines += [f"\n- Verdict pullback list vs the rest: {v}",
              f"- **T3 verdict: {'PASS' if v == 'FINDING: better' else 'FAIL'}; "
              f"tradeable after cost: {'YES' if tradeable else 'NO'}**"]
    return lines


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labeled", type=Path, default=ROOT / "data" / "research" / "universe" / "labeled")
    p.add_argument("--from", dest="start", default="2026-07-01")
    p.add_argument("--to", dest="end", default="2026-08-31")
    p.add_argument("--out", type=Path, default=ROOT / "reports" / "validate_m17.md")
    a = p.parse_args(argv)
    rows = load_rows(a.labeled, a.start, a.end, "2026-09-29.m15", extra_prefixes=("net_",))
    days = sorted({r["day"] for r in rows})
    text = "\n".join([
        f"# Validation tests (DECISIONS #27) — whole market, {a.start}..{a.end}", "",
        f"Panel rows {len(rows)} · days {len(days)} · each test run once, pre-registered.", "",
        "## T1 — less weight on extension (IC = within-day rank correlation with excess return)", "",
        *t1(rows), "",
        "## T2 — lunch penalty", "", *t2(rows), "",
        "## T3 — pullback-only list (EMA_PULLBACK or GAP_AND_GO triggered, DAY)", "", *t3(rows), ""])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text, encoding="utf-8")
    print(f"report -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
