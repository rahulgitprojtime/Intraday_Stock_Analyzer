"""Recommendation strategy — M10 (DECISIONS #20), on the simulated broker (#29).

Turns the existing ranked recommendations into simulated entries; it never
re-scores. Each minute `recs_at(as_of)` returns the list the dashboard
would show at `as_of` (computed from bars closed by then):

1. a qualifying recommendation (policy.eligibility) becomes a market BUY
   of `quantity` shares with a stop/target bracket (risk.entry_bracket);
   the broker fills it at the next bar's open (backtest) or next tick
   (paper) and checks stop/target from then on;
2. top-list signals refused by a limit, by the broker, or never filled
   are journalled as MISSED once per (symbol, reason);
3. entries and exits go to the append-only journal with the full
   entry-time snapshot, so the M10 daily report keeps working.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from src.paper.journal import Journal, trade_id
from src.paper.orders import Bracket, Fill, Order, OrderStatus, Side
from src.paper.policy import PaperConfig, eligibility
from src.paper.risk import entry_bracket
from src.paper.strategy import Strategy, StrategyContext

# Limit refusals worth recording; quality refusals (low score, not
# triggered, ...) are not signals and would flood the journal.
LIMIT_REASONS = ("symbol traded today", "max open positions", "after no_entry_after")
ENTRY_TAG = "ENTRY"


class RecommendationStrategy(Strategy):
    name = "RECOMMENDATION"

    def __init__(self, cfg: PaperConfig, recs_at, daily_atr: dict, journal: Journal,
                 run_id: str, versions: dict, source: str) -> None:
        self.cfg, self.recs_at, self.daily_atr = cfg, recs_at, daily_atr
        self.journal, self.run_id, self.versions, self.source = journal, run_id, versions, source
        self._reset()

    def _reset(self) -> None:
        self._pending: dict[str, tuple[dict, datetime, Order, str, Bracket]] = {}
        self._open: dict[str, dict] = {}
        self._traded: dict[str, int] = {}
        self._missed_seen: set = set()

    def params(self) -> dict:
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in asdict(self.cfg).items()}

    def on_day_start(self, day: date, ctx: StrategyContext) -> None:
        self._reset()

    def on_resume(self, orders: list[dict], fills: list[dict], ctx: StrategyContext) -> None:
        """A restarted worker: count today's entries and re-attach journal
        trades of still-open positions so their exits are journalled."""
        for r in orders:
            if r["tag"] == ENTRY_TAG and r["status"] == OrderStatus.FILLED.value:
                self._traded[r["symbol"]] = self._traded.get(r["symbol"], 0) + 1
        held = ctx.broker.positions()
        charges = {f["order_id"]: f["total_charges"] for f in fills}
        entry_charges = {r["symbol"]: charges.get(r["id"], 0.0) for r in orders
                         if r["tag"] == ENTRY_TAG and r["status"] == OrderStatus.FILLED.value}
        for t in self.journal.trades():
            sym = t["symbol"]
            if t.get("exit_timestamp") is None and sym in held:      # journal is per day
                self._open[sym] = {"tid": t["trade_id"], "entry": t["entry_price"],
                                   "at": datetime.fromisoformat(t["entry_timestamp"]),
                                   "risk": t["initial_risk"],
                                   "charges": entry_charges.get(sym, 0.0)}

    def on_bars(self, as_of: datetime, bars: dict, ctx: StrategyContext) -> None:
        self._drop_dead_orders()
        recs = self.recs_at(as_of)
        in_use = set(ctx.broker.positions()) | set(self._pending)
        for rec in sorted(recs, key=lambda r: (r.get("rank") is None, r.get("rank") or 0)):
            sym = rec["symbol"]
            reason = eligibility(rec, self.cfg, in_use, self._traded, as_of.time())
            if reason is None:
                bracket, method = entry_bracket(self.daily_atr.get(sym), self.cfg)
                o = ctx.broker.place_order(sym, Side.BUY, self.cfg.quantity, tag=ENTRY_TAG,
                                           bracket=bracket)
                if o.status is OrderStatus.REJECTED:
                    self._missed(rec, as_of, f"broker: {o.reason}")
                    continue
                self._pending[sym] = (rec, as_of, o, method, bracket)
                in_use.add(sym)
            elif reason.startswith(LIMIT_REASONS):
                self._missed(rec, as_of, reason)

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        sym = fill.symbol
        if fill.tag == ENTRY_TAG and sym in self._pending:
            rec, signal_at, _, method, bracket = self._pending.pop(sym)
            stop, target = bracket.levels(fill.price)
            tid = trade_id(self.run_id, signal_at.date().isoformat(), sym, self.cfg.mode,
                           signal_at.isoformat(), self.versions["strategy_version"])
            self._open[sym] = {"tid": tid, "entry": fill.price, "at": fill.at,
                               "risk": fill.price - stop, "charges": fill.charges.total}
            self._traded[sym] = self._traded.get(sym, 0) + 1
            self.journal.record_entry(self._entry_record(tid, rec, signal_at, fill, stop, target,
                                                         method))
        elif fill.side is Side.SELL and sym in self._open:
            self._record_exit(self._open.pop(sym), fill)

    def on_day_end(self, day: date, ctx: StrategyContext) -> None:
        self._drop_dead_orders()
        for rec, signal_at, *_ in self._pending.values():
            self._missed(rec, signal_at, "no bar after signal (no fill)")
        self._pending.clear()

    # -- internals ----------------------------------------------------------------

    def _drop_dead_orders(self) -> None:
        for sym, (rec, signal_at, o, *_) in list(self._pending.items()):
            if o.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                del self._pending[sym]
                reason = ("no bar after signal (no fill)" if o.reason == "square-off"
                          else f"broker: {o.reason}")
                self._missed(rec, signal_at, reason)

    def _record_exit(self, pos: dict, fill: Fill) -> None:
        q = fill.quantity
        gross = round((fill.price - pos["entry"]) * q, 4)
        charges = round(pos["charges"] + fill.charges.total, 4)
        net = round(gross - charges, 4)                     # reconciles with stored fields
        self.journal.record_exit(pos["tid"], {
            "exit_timestamp": fill.at.isoformat(), "exit_price": fill.price,
            "exit_reason": fill.tag, "exit_note": fill.note,
            "gross_pnl": gross, "charges": charges, "net_pnl": net,
            "pnl_percent": round(net / (pos["entry"] * q) * 100, 4),
            "risk_multiple": round(net / q / pos["risk"], 4) if pos["risk"] > 0 else None,
            "holding_minutes": int((fill.at - pos["at"]).total_seconds() // 60),
        })

    def _missed(self, rec: dict, at: datetime, reason: str) -> None:
        key = (rec["symbol"], reason)
        if key in self._missed_seen:
            return
        self._missed_seen.add(key)
        self.journal.record_missed({
            "date": at.date().isoformat(), "symbol": rec["symbol"], "mode": self.cfg.mode,
            "signal_at": at.isoformat(), "reason": reason, "score": rec["score"],
            "category": rec["category"], "rank": rec.get("rank"),
            "best_setup": rec["setup"].get("best"), **self.versions,
        })

    def _entry_record(self, tid: str, r: dict, signal_at: datetime, fill: Fill, stop: float,
                      target: float, method: str) -> dict:
        return {
            "trade_id": tid, "date": signal_at.date().isoformat(), "symbol": fill.symbol,
            "mode": self.cfg.mode, "direction": "LONG", "signal_at": signal_at.isoformat(),
            "entry_timestamp": fill.at.isoformat(), "entry_price": fill.price,
            "stop_loss": stop, "target": target, "stop_method": method,
            "target_method": f"{self.cfg.target_r:g}R",
            "initial_risk": round(fill.price - stop, 4), "risk_reward": self.cfg.target_r,
            "quantity": fill.quantity,
            "recommendation_score": r["score"], "category": r["category"], "rank": r.get("rank"),
            "best_setup": r["setup"].get("best"), "setup_state": r["setup"].get("best_state"),
            "setup_family_confluence": r["setup"].get("confluence_families"),
            "in_play_score": r["quantitative"].get("in_play_score"),
            "market_context": r.get("market_context"), "sector_context": r.get("sector_context"),
            "microstructure": r["quantitative"].get("microstructure"),
            "qualitative_context": r.get("qualitative"), "prerequisites": r.get("prerequisites"),
            "entry_reason": r.get("prerequisites_summary"), "data_source": self.source,
            "replay_or_live": self.source, **self.versions,
        }
