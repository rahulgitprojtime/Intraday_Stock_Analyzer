"""Daily paper-trading report — M10 (DECISIONS #20).

Writes `<root>/<date>/{paper_trades.json, daily_report.json,
daily_report.md}` with every trade (losses included) and the missed-signal
count. Wording is observational: breakdowns are associations in a small
sample, not causes, and results are not probabilities of profit.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.paper.metrics import NA, breakdowns, summarize


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _table(title: str, groups: dict) -> list[str]:
    lines = [f"### {title}", "", "| Group | Trades | Win rate % | Avg R | Net P&L | Note |",
             "|---|---|---|---|---|---|"]
    for k, g in groups.items():
        lines.append(f"| {k} | {g['trades']} | {_fmt(g['win_rate'])} | {_fmt(g['average_r'])} | "
                     f"{_fmt(g['net_pnl'])} | {g['sample_warning'] or ''} |")
    return lines + [""]


def write_daily_report(root: str | Path, day: str, trades: list[dict], missed: list[dict],
                       meta: dict) -> Path:
    out = Path(root) / day
    out.mkdir(parents=True, exist_ok=True)
    summary, groups = summarize(trades), breakdowns(trades)
    data = {"date": day, "meta": meta, "summary": summary, "breakdowns": groups,
            "missed_signals": len(missed), "trades": trades}
    (out / "paper_trades.json").write_text(json.dumps(trades, indent=1, ensure_ascii=False),
                                           encoding="utf-8")
    (out / "daily_report.json").write_text(json.dumps(data, indent=1, ensure_ascii=False),
                                           encoding="utf-8")
    md = [f"# PAPER TRADING / SIMULATION — {day}", "",
          "Simulated positions on real candles; no orders were placed. Results are "
          "measurements, not probabilities of profit.", "",
          f"Strategy {meta.get('strategy_version', NA)} · config {meta.get('config_hash', NA)} · "
          f"commit {meta.get('git_commit', NA)} · source {meta.get('replay_or_live', NA)}", ""]
    if summary["trades"] == 0:
        md += ["**NO TRADES** — no recommendation met the paper-entry policy.", ""]
    md += ["## Overall", "", "| Metric | Value |", "|---|---|"]
    md += [f"| {k.replace('_', ' ')} | {_fmt(v)} |" for k, v in summary.items()]
    md += ["", f"Missed signals (qualified but refused by a limit): {len(missed)}", ""]
    md += ["## Breakdowns", "",
           "Observed association only, in a small sample; not evidence of cause.", ""]
    for name, g in groups.items():
        md += _table(name.replace("_", " ").title(), g)
    md += ["## All trades", "", "| Symbol | Setup | Entry | Exit | Reason | Net P&L | R |",
           "|---|---|---|---|---|---|---|"]
    for t in sorted(trades, key=lambda t: t["entry_timestamp"]):
        md.append(f"| {t['symbol']} | {t.get('best_setup')} | {t['entry_timestamp'][11:16]} | "
                  f"{(t.get('exit_timestamp') or '')[11:16]} | {t.get('exit_reason')} | "
                  f"{_fmt(t.get('net_pnl'))} | {_fmt(t.get('risk_multiple'))} |")
    (out / "daily_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return out
