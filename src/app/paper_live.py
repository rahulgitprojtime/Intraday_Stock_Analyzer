"""Live paper trading inside the worker (DECISIONS #29). SIMULATION ONLY.

One strategy on a `PaperBroker`:
- a 1 s poller reads the latest LTP per symbol from the worker's FeedStore
  and offers each new price to the broker as a tick, so orders fill on the
  first live price after they were placed (plus slippage);
- once a minute the worker hands over the minute's state; the closed 1-min
  bars are read from the candle cache the worker has just refreshed (no
  extra API calls) and the strategy runs on them.
Everything is written to the paper ledger (`paper.yaml ledger`) for the
dashboard. Nothing is ever sent to Groww. Orders for symbols without live
ticks (not subscribed) stay open until square-off.

Several strategies can run side by side (`paper.yaml strategies`), each
on its own broker and ledger run. A run is one per strategy per day
(`paper:<day>:<strategy>`): when the worker restarts mid-day it RESUMES
that run — positions, cash, closed trades and the open stop/target orders
are rebuilt from the ledger (DECISIONS #30), so a crash never orphans a
position. Open positions are pinned in the dynamic universe so their
prices keep streaming. At stop, a day report is written to
`reports/paper_<day>.md`.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from src.paper.broker import BrokerConfig, PaperBroker
from src.paper.costs import CostModel
from src.paper.ledger import Ledger
from src.paper.orders import Bar
from src.paper.session import TradingSession
from src.utils.config import load_yaml

ONE_MIN = timedelta(minutes=1)


class LivePaper:
    def __init__(self, session: TradingSession, feed_store, mode: str = "DAY",
                 poll_seconds: float = 1.0, clock=datetime.now) -> None:
        self.session, self.feed_store, self.mode = session, feed_store, mode
        self.poll_seconds, self.clock = poll_seconds, clock
        self.lock = threading.Lock()
        self.state: dict | None = None
        self._last_ltp: dict[str, float] = {}
        self._seen: dict[str, datetime] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []
        self.resumed: list[str] | None = None     # symbols held when a restart resumed the run

    # -- ticks --------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="paper-ticks", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.pump(self.clock())
            except Exception as exc:                  # never kill the worker
                self.errors.append(f"paper ticks: {type(exc).__name__}: {exc}")

    def pump(self, now: datetime) -> int:
        """Offer every changed LTP to the broker; returns fills."""
        if self.feed_store is None:
            return 0
        snap = self.feed_store.snapshot(now)
        n = 0
        with self.lock:
            for sym, st in sorted(snap.symbols.items()):
                if st.ltp is None or self._last_ltp.get(sym) == st.ltp:
                    continue
                self._last_ltp[sym] = st.ltp
                n += len(self.session.on_tick(Bar.tick(sym, now, st.ltp)))
        return n

    # -- minutes ------------------------------------------------------------------

    def recs(self, as_of: datetime) -> list[dict]:
        """The worker's recommendations for the current minute."""
        return ((self.state or {}).get("modes") or {}).get(self.mode, [])

    def on_minute(self, ctx, as_of: datetime, state: dict) -> None:
        self.state = state
        cache = getattr(ctx.source, "cache", None)
        bars = []
        indices = [i for i in (getattr(ctx, "index", None), getattr(ctx, "bank_index", None))
                   if i is not None]
        sctx = self.session.ctx                    # what the strategies see (#31)
        sctx.meta = {"sentiment": state.get("sentiment"),
                     "indices": [i.trading_symbol for i in indices],
                     "preps": {s: pr.prep for s, pr in (ctx.preps or {}).items()
                               if pr is not None and getattr(pr, "prep", None) is not None}}
        for sym, prep in sctx.meta["preps"].items():
            sctx.prev_close[sym] = prep.prev_close
        for sym, prep in (getattr(ctx, "index_preps", None) or {}).items():
            if prep is not None:
                sctx.prev_close[sym] = prep.prev_close
        for inst in list(ctx.stocks) + indices:
            candles = cache.load(inst, as_of.date()) if cache is not None else \
                ctx.source.minute_candles(inst, as_of)
            for c in candles:
                if c.is_complete and c.timestamp + ONE_MIN <= as_of and \
                        c.timestamp > self._seen.get(inst.trading_symbol, datetime.min):
                    bars.append(Bar.from_candle(c))
                    self._seen[inst.trading_symbol] = c.timestamp
        with self.lock:
            self.session.step(as_of, bars)

    def open_symbols(self) -> set:
        with self.lock:
            b = self.session.broker
            return set(b.positions()) | {o.symbol for o in b.open_orders()}

    def stop(self, day: date, close_ledger: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self.lock:
            self.session.end_day(day)
        if close_ledger and self.session.ledger is not None:
            self.session.ledger.close()


class MultiPaper:
    """Several LivePaper sessions driven by the same worker minute/feed."""

    def __init__(self, sessions: list[LivePaper], report_dir: Path | None = None,
                 ledger_path: Path | None = None) -> None:
        self.sessions, self.report_dir, self.ledger_path = sessions, report_dir, ledger_path

    @property
    def run_ids(self) -> list[str]:
        return [s.session.broker.run_id for s in self.sessions]

    @property
    def errors(self) -> list[str]:
        return [e for s in self.sessions for e in s.errors]

    def start(self) -> None:
        for s in self.sessions:
            s.start()

    def on_minute(self, ctx, as_of: datetime, state: dict) -> None:
        errs = []
        for s in self.sessions:
            try:
                s.on_minute(ctx, as_of, state)
            except Exception as exc:                  # one strategy never stops another
                errs.append(f"{s.session.strategy.name}: {type(exc).__name__}: {exc}")
        if errs:
            raise RuntimeError("; ".join(errs))

    def open_symbols(self) -> set:
        return set().union(*(s.open_symbols() for s in self.sessions)) if self.sessions else set()

    def stop(self, day: date) -> None:
        for s in self.sessions:
            s.stop(day, close_ledger=False)
        if self.sessions and self.sessions[0].session.ledger is not None:
            self.sessions[0].session.ledger.close()      # one shared connection
        if self.report_dir is not None and self.ledger_path is not None:
            from src.paper.day_report import write_day_report
            write_day_report(self.ledger_path, day, self.report_dir)


class _AtrView:
    """Daily ATR per symbol from the worker's preps (the universe can grow)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def get(self, symbol: str):
        pr = self.ctx.preps.get(symbol)
        return pr.prep.atr if pr else None


def strategy_names(raw: dict) -> list[str]:
    names = raw.get("strategies") or [raw.get("strategy", "recommendation")]
    return [str(n).lower() for n in names]


def build_live_paper(ctx, day: date, raw: dict | None = None, now: datetime | None = None,
                     name: str | None = None, ledger: Ledger | None = None) -> LivePaper:
    """One paper session from config/paper.yaml for the live worker; resumes
    today's run of the same strategy if one exists (worker restart)."""
    from src.paper.journal import Journal, version_info
    from src.paper.policy import PaperConfig
    from src.paper.strategies.orb import OpeningRangeBreakout
    from src.paper.strategies.recommendation import RecommendationStrategy
    from src.paper.strategies.scalp import ScalpConfig, ScalpPriceAction
    from src.paper.strategies.trend import IntradayTrend, TrendConfig
    from src.market.sentiment import SentimentConfig

    raw = raw or load_yaml("paper.yaml")
    name = (name or strategy_names(raw)[0]).lower()
    run_id = f"paper:{day.isoformat()}:{name}"            # one run per strategy per day
    ledger = ledger or Ledger(raw["ledger"])   # strategies share one connection (one writer)
    broker = PaperBroker(BrokerConfig.from_dict(raw["broker"]),
                         CostModel.from_dict(load_yaml("costs.yaml")), ledger, run_id)
    holder: dict = {}
    sent_cfg = SentimentConfig.from_dict((load_yaml("universe.yaml").get("scan") or {})
                                         .get("sentiment"))
    if name == "scalp":
        strategy = ScalpPriceAction(ScalpConfig.from_dict(raw.get("scalp")), sent_cfg)
    elif name == "trend":
        strategy = IntradayTrend(TrendConfig.from_dict(raw.get("trend")), sent_cfg)
    elif name == "orb":
        strategy = OpeningRangeBreakout.from_dict(raw["orb"])
    elif name == "recommendation":
        cfg = PaperConfig.from_dict(raw)
        journal = Journal(ledger.path.parent / "journal" / f"{day.isoformat()}.jsonl")
        strategy = RecommendationStrategy(cfg, lambda t: holder["live"].recs(t), _AtrView(ctx),
                                          journal, run_id, version_info(), "live")
    else:
        raise ValueError("paper.yaml strategy must be scalp, trend, recommendation or orb, "
                         f"got {name!r}")
    resuming = ledger.has_run(run_id)
    if not resuming:
        ledger.start_run(run_id, broker.mode, strategy.name, broker.cfg.starting_capital,
                         strategy.params())
    session = TradingSession(strategy, broker, ledger=ledger, fill_on_bars=False)
    session.start_day(day)
    holder["live"] = live = LivePaper(session, ctx.feed_store, str(raw.get("mode", "DAY")))
    if resuming:
        orders, fills = ledger.orders(run_id), ledger.fills(run_id)
        live.resumed = broker.resume(orders, fills, ledger.trades(run_id))
        strategy.on_resume(orders, fills, session.ctx)
        ledger.commit()
    return live


def build_live_papers(ctx, day: date, raw: dict | None = None,
                      now: datetime | None = None) -> MultiPaper:
    """Every strategy in paper.yaml `strategies` (or the single `strategy`)."""
    raw = raw or load_yaml("paper.yaml")
    ledger = Ledger(raw["ledger"])
    sessions = [build_live_paper(ctx, day, raw, now, n, ledger) for n in strategy_names(raw)]
    report_dir = raw.get("report_dir", "reports")
    return MultiPaper(sessions, Path(report_dir) if report_dir else None, Path(raw["ledger"]))
