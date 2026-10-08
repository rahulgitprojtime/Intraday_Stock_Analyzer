"""SQLite ledger — DECISIONS #29. SIMULATION ONLY.

One file can hold many runs (a backtest, a paper-trading day); every row is
keyed by `run_id`. Starting a run again replaces that run's rows only.
Writes are buffered until `commit()` (the session commits once a minute);
WAL mode lets the dashboard read while the worker writes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from src.paper.orders import Fill, Order, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, mode TEXT, strategy TEXT, started_at TEXT,
    starting_capital REAL, params TEXT);
CREATE TABLE IF NOT EXISTS orders (
    run_id TEXT, id TEXT, symbol TEXT, side TEXT, quantity INTEGER, type TEXT,
    limit_price REAL, trigger_price REAL, tag TEXT, parent_id TEXT, oco_id TEXT,
    status TEXT, reason TEXT, placed_at TEXT, fill_price REAL, filled_at TEXT,
    PRIMARY KEY (run_id, id));
CREATE TABLE IF NOT EXISTS fills (
    run_id TEXT, order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL,
    at TEXT, tag TEXT, note TEXT, brokerage REAL, stt REAL, exchange_txn REAL,
    sebi_fee REAL, ipft REAL, gst REAL, stamp_duty REAL, total_charges REAL);
CREATE TABLE IF NOT EXISTS positions (
    run_id TEXT, symbol TEXT, quantity INTEGER, avg_price REAL, realized_pnl REAL,
    charges REAL, last_price REAL, updated_at TEXT, PRIMARY KEY (run_id, symbol));
CREATE TABLE IF NOT EXISTS trades (
    run_id TEXT, symbol TEXT, entry_at TEXT, exit_at TEXT, quantity INTEGER,
    entry_price REAL, exit_price REAL, gross_pnl REAL, charges REAL, net_pnl REAL,
    entry_tag TEXT, exit_tag TEXT, holding_minutes INTEGER, direction TEXT,
    stop_loss REAL, target REAL);
CREATE TABLE IF NOT EXISTS daily_pnl (
    run_id TEXT, day TEXT, trades INTEGER, gross_pnl REAL, charges REAL, net_pnl REAL,
    end_equity REAL, PRIMARY KEY (run_id, day));
CREATE TABLE IF NOT EXISTS equity (
    run_id TEXT, at TEXT, equity REAL, PRIMARY KEY (run_id, at));
"""
TABLES = ("runs", "orders", "fills", "positions", "trades", "daily_pnl", "equity")
TRADE_FIELDS = ("symbol", "entry_at", "exit_at", "quantity", "entry_price", "exit_price",
                "gross_pnl", "charges", "net_pnl", "entry_tag", "exit_tag", "holding_minutes",
                "direction", "stop_loss", "target")
# Columns added after the first release (DECISIONS #30); older files get them on open.
ADDED_COLUMNS = {"trades": {"direction": "TEXT", "stop_loss": "REAL", "target": "REAL"}}


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def daily_row(day: date, trades: list[dict], end_equity: float) -> dict:
    """P&L of the round trips that closed on `day`."""
    today = [t for t in trades if t["exit_at"][:10] == day.isoformat()]
    return {"day": day.isoformat(), "trades": len(today),
            "gross_pnl": round(sum(t["gross_pnl"] for t in today), 4),
            "charges": round(sum(t["charges"] for t in today), 4),
            "net_pnl": round(sum(t["net_pnl"] for t in today), 4),
            "end_equity": round(end_equity, 4)}


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for table, cols in ADDED_COLUMNS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.conn.commit()

    # -- writes -----------------------------------------------------------------

    def start_run(self, run_id: str, mode: str, strategy: str, starting_capital: float,
                  params: dict | None = None) -> None:
        for t in TABLES:
            self.conn.execute(f"DELETE FROM {t} WHERE run_id = ?", (run_id,))  # noqa: S608
        self.conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                          (run_id, mode, strategy, datetime.now().isoformat(timespec="seconds"),
                           starting_capital, json.dumps(params or {}, sort_keys=True)))
        self.conn.commit()

    def upsert_order(self, run_id: str, o: Order) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, o.id, o.symbol, o.side.value, o.quantity, o.type.value, o.limit_price,
             o.trigger_price, o.tag, o.parent_id, o.oco_id, o.status.value, o.reason,
             _iso(o.placed_at), o.fill_price, _iso(o.filled_at)))

    def add_fill(self, run_id: str, f: Fill) -> None:
        c = f.charges
        self.conn.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, f.order_id, f.symbol, f.side.value, f.quantity, f.price, _iso(f.at), f.tag,
             f.note, c.brokerage, c.stt, c.exchange_txn, c.sebi_fee, c.ipft, c.gst,
             c.stamp_duty, c.total))

    def upsert_position(self, run_id: str, p: Position, at: datetime) -> None:
        self.conn.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?)",
                          (run_id, p.symbol, p.quantity, p.avg_price, p.realized_pnl, p.charges,
                           p.last_price, _iso(at)))

    def add_trade(self, run_id: str, t: dict) -> None:
        cols = ", ".join(("run_id",) + TRADE_FIELDS)
        self.conn.execute(f"INSERT INTO trades ({cols}) VALUES (?{',?' * len(TRADE_FIELDS)})",
                          (run_id, *(t.get(k) for k in TRADE_FIELDS)))

    def record_equity(self, run_id: str, at: datetime, equity: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?)",
                          (run_id, _iso(at), equity))

    def record_day(self, run_id: str, day: date, trades: list[dict], end_equity: float) -> dict:
        row = daily_row(day, trades, end_equity)
        self.conn.execute("INSERT OR REPLACE INTO daily_pnl VALUES (?,?,?,?,?,?,?)",
                          (run_id, *row.values()))
        return row

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- reads ------------------------------------------------------------------

    def _rows(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args)]

    def has_run(self, run_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() \
            is not None

    def runs(self) -> list[dict]:
        rows = self._rows("SELECT * FROM runs ORDER BY started_at DESC, run_id")
        return [r | {"params": json.loads(r["params"] or "{}")} for r in rows]

    def orders(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM orders WHERE run_id = ? ORDER BY id", run_id)

    def fills(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM fills WHERE run_id = ? ORDER BY rowid", run_id)

    def positions(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM positions WHERE run_id = ? ORDER BY symbol", run_id)

    def trades(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM trades WHERE run_id = ? ORDER BY rowid", run_id)

    def daily_pnl(self, run_id: str) -> list[dict]:
        return self._rows("SELECT * FROM daily_pnl WHERE run_id = ? ORDER BY day", run_id)

    def equity_curve(self, run_id: str) -> list[tuple[str, float]]:
        return [(r["at"], r["equity"]) for r in
                self._rows("SELECT at, equity FROM equity WHERE run_id = ? ORDER BY at", run_id)]
