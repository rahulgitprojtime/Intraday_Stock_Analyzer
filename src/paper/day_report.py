"""Paper-trading day report — DECISIONS #30. SIMULATION ONLY.

One markdown file per day (`reports/paper_<day>.md`) from the paper
ledger: for every live paper run of that day (one per strategy), each
round trip with its direction, entry, stop-loss, target, exit, exit
reason and gross/charges/net P&L, then the day's totals. Written by the
worker when it stops and by `scripts/paper_day_report.py` (any time).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.paper.ledger import Ledger

EXIT_LABEL = {"STOP_LOSS": "stop-loss", "TARGET": "target", "SQUARE_OFF": "square-off 15:15"}


def _rs(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _hm(iso: str | None) -> str:
    return iso[11:16] if iso else "—"


def day_runs(db: Ledger, day: date) -> list[dict]:
    prefix = f"paper:{day.isoformat()}:"
    return sorted((r for r in db.runs() if r["run_id"].startswith(prefix)),
                  key=lambda r: r["run_id"])


def summarize(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["net_pnl"] > 0]
    return {"trades": len(trades), "wins": len(wins), "losses": len(trades) - len(wins),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "gross_pnl": round(sum(t["gross_pnl"] for t in trades), 2),
            "charges": round(sum(t["charges"] for t in trades), 2),
            "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
            "long": sum(1 for t in trades if (t.get("direction") or "LONG") == "LONG"),
            "short": sum(1 for t in trades if t.get("direction") == "SHORT")}


def render(db: Ledger, day: date) -> str:
    runs = day_runs(db, day)
    out = [f"# Paper trading — {day.isoformat()}",
           "",
           "SIMULATION ONLY: simulated fills on live Groww prices; no order was sent to "
           "Groww. Net P&L is after the modelled Groww charges (brokerage, STT, exchange, "
           "SEBI, GST, stamp duty) and slippage. One day is description, not evidence.",
           ""]
    if not runs:
        return "\n".join(out + ["No paper runs recorded for this day."]) + "\n"
    all_trades = []
    for r in runs:
        trades = db.trades(r["run_id"])
        all_trades += trades
        s = summarize(trades)
        eq = db.daily_pnl(r["run_id"])
        end_eq = eq[-1]["end_equity"] if eq else None
        out += [f"## {r['strategy']} (`{r['run_id']}`)", "",
                f"Trades **{s['trades']}** (long {s['long']}, short {s['short']}) · "
                f"wins {s['wins']} / losses {s['losses']}"
                + (f" ({s['win_rate']}%)" if s["win_rate"] is not None else "")
                + f" · gross ₹{_rs(s['gross_pnl'])} · charges ₹{_rs(s['charges'])} · "
                f"**net ₹{_rs(s['net_pnl'])}**"
                + (f" · end equity ₹{_rs(end_eq)} (start ₹{_rs(r['starting_capital'])})"
                   if end_eq is not None else ""), ""]
        if trades:
            out += ["| Symbol | Dir | Qty | Entry | Entry ₹ | Stop ₹ | Target ₹ | Exit | Exit ₹ "
                    "| Exit reason | Gross ₹ | Charges ₹ | Net ₹ |",
                    "|---|---|--:|---|--:|--:|--:|---|--:|---|--:|--:|--:|"]
            for t in trades:
                out.append(
                    f"| {t['symbol']} | {t.get('direction') or 'LONG'} | {t['quantity']} "
                    f"| {_hm(t['entry_at'])} | {_rs(t['entry_price'])} | {_rs(t.get('stop_loss'))} "
                    f"| {_rs(t.get('target'))} | {_hm(t['exit_at'])} | {_rs(t['exit_price'])} "
                    f"| {EXIT_LABEL.get(t['exit_tag'], t['exit_tag'])} | {_rs(t['gross_pnl'])} "
                    f"| {_rs(t['charges'])} | **{_rs(t['net_pnl'])}** |")
            out.append("")
        held = [p for p in db.positions(r["run_id"]) if p["quantity"]]
        if held:
            out += ["Still open in the ledger (worker stopped before square-off):", ""]
            out += [f"- {p['symbol']}: {p['quantity']} @ ₹{_rs(p['avg_price'])}, "
                    f"last ₹{_rs(p['last_price'])}" for p in held]
            out.append("")
    if len(runs) > 1:
        s = summarize(all_trades)
        out += ["## All strategies", "",
                f"Trades {s['trades']} · net ₹{_rs(s['net_pnl'])} "
                f"(gross ₹{_rs(s['gross_pnl'])}, charges ₹{_rs(s['charges'])})", ""]
    return "\n".join(out)


def write_day_report(ledger_path: str | Path, day: date, out_dir: str | Path) -> Path:
    db = Ledger(ledger_path)
    try:
        text = render(db, day)
    finally:
        db.close()
    out = Path(out_dir) / f"paper_{day.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
