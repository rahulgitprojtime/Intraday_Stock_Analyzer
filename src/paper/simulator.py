"""Virtual position lifecycle — M10 (DECISIONS #20). SIMULATION ONLY.

Runs on the same minute clock as the recommendation engine. At each tick
`as_of` it receives the ranked recommendations for `as_of` and only the
1-min bars that had closed by `as_of` — never later ones:

1. pending signals fill at the OPEN of the first bar starting at/after
   the signal (+ slippage); stop/target are fixed from that fill;
2. open positions are checked bar by bar: gap below stop → exit at the
   (worse) open; low ≤ stop → STOP_LOSS (also when the same bar touches
   the target: sequence unknown, stop assumed first); high ≥ target →
   TARGET; at `eod_exit` → END_OF_DAY at the last closed bar's close;
3. new qualifying signals become pending; top-list signals refused by a
   limit are journalled as MISSED once per (symbol, reason).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.paper.journal import Journal, trade_id
from src.paper.policy import PaperConfig, eligibility
from src.paper.risk import initial_levels

ONE_MIN = timedelta(minutes=1)
# Limit refusals worth recording; quality refusals (low score, not
# triggered, ...) are not signals and would flood the journal.
LIMIT_REASONS = ("symbol traded today", "max open positions", "after no_entry_after")


@dataclass
class _Pending:
    rec: dict
    signal_at: datetime


@dataclass
class _Position:
    tid: str
    symbol: str
    entry_at: datetime
    entry: float
    stop: float
    target: float
    risk: float
    last_checked: datetime | None = None


@dataclass
class PaperSimulator:
    cfg: PaperConfig
    journal: Journal
    run_id: str
    versions: dict
    source: str                                   # "replay" | "live"
    _pending: dict = field(default_factory=dict)  # symbol -> _Pending
    _open: dict = field(default_factory=dict)     # symbol -> _Position
    _traded: dict = field(default_factory=dict)   # symbol -> entries today
    _missed_seen: set = field(default_factory=set)
    _last_bar: dict = field(default_factory=dict)

    # -- per tick ---------------------------------------------------------------

    def step(self, as_of: datetime, recs: list[dict], bars: dict, daily_atr: dict) -> None:
        for sym, bs in bars.items():
            if bs:
                self._last_bar[sym] = bs[-1]
        self._fill_pending(bars, daily_atr)
        for pos in list(self._open.values()):
            self._check_exits(pos, bars.get(pos.symbol, []))
        if as_of.time() >= self.cfg.eod_exit:
            self._close_all("END_OF_DAY")
            return
        self._new_signals(as_of, recs)

    def finish(self) -> None:
        """Session over: unfilled signals are MISSED, open positions close."""
        for p in self._pending.values():
            self._missed(p.rec, p.signal_at, "no bar after signal (no fill)")
        self._pending.clear()
        self._close_all("END_OF_DAY")

    # -- internals ----------------------------------------------------------------

    def _slip(self, price: float, sign: int) -> float:
        return price * (1 + sign * self.cfg.slippage_bps / 10_000)

    def _fill_pending(self, bars: dict, daily_atr: dict) -> None:
        for sym, p in list(self._pending.items()):
            nxt = next((b for b in bars.get(sym, []) if b.timestamp >= p.signal_at), None)
            if nxt is None:
                continue
            del self._pending[sym]
            fill = round(self._slip(nxt.open, +1), 4)   # journal stores exactly this
            stop, target, method = initial_levels(fill, daily_atr.get(sym), self.cfg)
            pos = _Position(trade_id(self.run_id, p.signal_at.date().isoformat(), sym,
                                     self.cfg.mode, p.signal_at.isoformat(),
                                     self.versions["strategy_version"]),
                            sym, nxt.timestamp, fill, stop, target, fill - stop)
            self.journal.record_entry(self._entry_record(pos, p, method))
            self._open[sym] = pos
            self._traded[sym] = self._traded.get(sym, 0) + 1
            self._check_exits(pos, bars.get(sym, []), first=nxt)

    def _check_exits(self, pos: _Position, bars: list, first=None) -> None:
        for b in bars:
            if b.timestamp < pos.entry_at or (pos.last_checked and b.timestamp <= pos.last_checked):
                continue
            pos.last_checked = b.timestamp
            end = b.timestamp + ONE_MIN
            entry_bar = b is first or b.timestamp == pos.entry_at
            if not entry_bar and b.open <= pos.stop:
                return self._exit(pos, b.open, end, "STOP_LOSS", "gapped through stop")
            if b.low <= pos.stop:
                note = ("stop and target touched in one bar: stop assumed first"
                        if b.high >= pos.target else None)
                return self._exit(pos, pos.stop, end, "STOP_LOSS", note)
            if b.high >= pos.target:
                return self._exit(pos, pos.target, end, "TARGET", None)
        return None

    def _close_all(self, reason: str) -> None:
        for pos in list(self._open.values()):
            last = self._last_bar.get(pos.symbol)
            price = last.close if last else pos.entry
            at = last.timestamp + ONE_MIN if last else pos.entry_at
            self._exit(pos, price, at, reason, None)

    def _exit(self, pos: _Position, raw_price: float, at: datetime, reason: str,
              note: str | None) -> None:
        q = self.cfg.quantity
        price = round(self._slip(raw_price, -1), 4)   # P&L from the stored price
        gross = round((price - pos.entry) * q, 4)
        charges = round(pos.entry * q * self.cfg.charges_pct_round_trip / 100, 4)
        net = round(gross - charges, 4)             # reconciles exactly with stored fields
        self.journal.record_exit(pos.tid, {
            "exit_timestamp": at.isoformat(), "exit_price": price,
            "exit_reason": reason, "exit_note": note,
            "gross_pnl": gross, "charges": charges, "net_pnl": net,
            "pnl_percent": round((price / pos.entry - 1) * 100
                                 - self.cfg.charges_pct_round_trip, 4),
            "risk_multiple": round(net / q / pos.risk, 4) if pos.risk > 0 else None,
            "holding_minutes": int((at - pos.entry_at).total_seconds() // 60),
        })
        del self._open[pos.symbol]

    def _new_signals(self, as_of: datetime, recs: list[dict]) -> None:
        open_syms = set(self._open) | set(self._pending)
        for rec in sorted(recs, key=lambda r: (r.get("rank") is None, r.get("rank") or 0)):
            reason = eligibility(rec, self.cfg, open_syms, self._traded, as_of.time())
            if reason is None:
                self._pending[rec["symbol"]] = _Pending(rec, as_of)
                open_syms.add(rec["symbol"])
            elif reason.startswith(LIMIT_REASONS):
                self._missed(rec, as_of, reason)

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

    def _entry_record(self, pos: _Position, p: _Pending, method: str) -> dict:
        r = p.rec
        return {
            "trade_id": pos.tid, "date": p.signal_at.date().isoformat(), "symbol": pos.symbol,
            "mode": self.cfg.mode, "direction": "LONG", "signal_at": p.signal_at.isoformat(),
            "entry_timestamp": pos.entry_at.isoformat(), "entry_price": pos.entry,
            "stop_loss": pos.stop, "target": pos.target, "stop_method": method,
            "target_method": f"{self.cfg.target_r:g}R", "initial_risk": round(pos.risk, 4),
            "risk_reward": self.cfg.target_r, "quantity": self.cfg.quantity,
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
