"""View model for the Paper trading page (DECISIONS #29). Pure functions over
the SQLite ledger; no Streamlit, no broker imports. SIMULATION ONLY."""

from __future__ import annotations

from pathlib import Path

from src.paper.ledger import Ledger
from src.paper.performance import performance

DISCLAIMER = ("SIMULATION ONLY: orders go to an in-process simulated broker and nothing is "
              "sent to Groww. P&L is after modelled costs and slippage. Results are "
              "measurements, not probabilities of profit.")


def open_ledger(path: str | Path) -> Ledger | None:
    return Ledger(path) if Path(path).exists() else None


def run_options(ledger: Ledger, mode: str | None = None) -> list[dict]:
    return [r for r in ledger.runs() if mode is None or r["mode"] == mode]


def run_report(ledger: Ledger, run: dict) -> dict:
    rid = run["run_id"]
    trades, daily = ledger.trades(rid), ledger.daily_pnl(rid)
    equity = ledger.equity_curve(rid)
    return {"metrics": performance(trades, equity, daily, run["starting_capital"]),
            "equity": [{"at": at, "equity": eq} for at, eq in equity],
            "daily": daily, "trades": trades}


def live_view(ledger: Ledger, run: dict) -> dict:
    rid = run["run_id"]
    positions = []
    for p in ledger.positions(rid):
        if not p["quantity"]:
            continue
        last = p["last_price"] if p["last_price"] is not None else p["avg_price"]
        positions.append({"symbol": p["symbol"], "quantity": p["quantity"],
                          "avg_price": round(p["avg_price"], 2), "last_price": round(last, 2),
                          "unrealized_pnl": round((last - p["avg_price"]) * p["quantity"], 2),
                          "updated_at": p["updated_at"]})
    trades = ledger.trades(rid)
    equity = ledger.equity_curve(rid)
    return {
        "positions": positions,
        "open_orders": [o for o in ledger.orders(rid) if o["status"] == "OPEN"],
        "fills": ledger.fills(rid), "trades": trades,
        "realized_net": round(sum(t["net_pnl"] for t in trades), 2),
        "charges": round(sum(f["total_charges"] for f in ledger.fills(rid)), 2),
        "unrealized": round(sum(p["unrealized_pnl"] for p in positions), 2),
        "equity": equity[-1][1] if equity else run["starting_capital"],
        "as_of": equity[-1][0] if equity else None,
        "equity_curve": [{"at": at, "equity": eq} for at, eq in equity],
    }
