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

Known limit: simulated positions live in memory. If the worker restarts
mid-day, a new ledger run starts (the earlier run's rows are kept, its
open positions are not carried over).
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from src.paper.backtest import ensure_cached, weekdays
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
        self._prior_loaded: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []

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

    def _prior(self, ctx, inst, day: date) -> list[Bar]:
        """The previous session's 1-min bars: from the candle cache, else one
        history request (cached). Empty when unavailable."""
        cache = getattr(ctx.source, "cache", None)
        if cache is None:
            return []
        days = weekdays(day - timedelta(days=7), day - timedelta(days=1))
        adapter = getattr(ctx.source, "adapter", None)
        if adapter is not None:
            ensure_cached(cache, adapter, inst, days, day)
        for d in reversed(days):
            bars = [Bar.from_candle(c) for c in cache.load(inst, d) if c.is_complete]
            if bars:
                return bars
        return []

    def on_minute(self, ctx, as_of: datetime, state: dict) -> None:
        self.state = state
        cache = getattr(ctx.source, "cache", None)
        bars = []
        strategy = self.session.strategy
        wanted = list(ctx.stocks) + [i for i in (getattr(ctx, "index", None),) if i is not None
                                     and i.trading_symbol in strategy.context_symbols]
        for inst in wanted:
            sym = inst.trading_symbol
            if strategy.needs_prior and sym not in self._prior_loaded:
                self._prior_loaded.add(sym)
                try:
                    prior = self._prior(ctx, inst, as_of.date())
                except Exception as exc:              # prior-day context is optional
                    prior = []
                    self.errors.append(f"paper prior {sym}: {type(exc).__name__}: {exc}")
                with self.lock:
                    self.session.ctx.set_prior(sym, prior)
        for inst in wanted:
            candles = cache.load(inst, as_of.date()) if cache is not None else \
                ctx.source.minute_candles(inst, as_of)
            for c in candles:
                if c.is_complete and c.timestamp + ONE_MIN <= as_of and \
                        c.timestamp > self._seen.get(inst.trading_symbol, datetime.min):
                    bars.append(Bar.from_candle(c))
                    self._seen[inst.trading_symbol] = c.timestamp
        with self.lock:
            self.session.step(as_of, bars)

    def stop(self, day: date) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self.lock:
            self.session.end_day(day)
        if self.session.ledger is not None:
            self.session.ledger.close()


class _AtrView:
    """Daily ATR per symbol from the worker's preps (the universe can grow)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def get(self, symbol: str):
        pr = self.ctx.preps.get(symbol)
        return pr.prep.atr if pr else None


def build_live_paper(ctx, day: date, raw: dict | None = None, now: datetime | None = None
                     ) -> LivePaper:
    """Paper session from config/paper.yaml for the live worker."""
    from src.paper.journal import Journal, version_info
    from src.paper.policy import PaperConfig
    from src.paper.strategies.orb import OpeningRangeBreakout
    from src.paper.strategies.playbook import IntradayPlaybook, PlaybookConfig
    from src.paper.strategies.recommendation import RecommendationStrategy

    raw = raw or load_yaml("paper.yaml")
    now = now or datetime.now()
    name = str(raw.get("strategy", "recommendation")).lower()
    run_id = f"paper:{day.isoformat()}:{name}:{now:%H%M%S}"   # a restart never wipes a run
    ledger = Ledger(raw["ledger"])
    broker = PaperBroker(BrokerConfig.from_dict(raw["broker"]),
                         CostModel.from_dict(load_yaml("costs.yaml")), ledger, run_id)
    holder: dict = {}
    if name == "orb":
        strategy = OpeningRangeBreakout.from_dict(raw["orb"])
    elif name == "playbook":
        strategy = IntradayPlaybook(PlaybookConfig.from_dict(raw["playbook"]))
    elif name == "recommendation":
        cfg = PaperConfig.from_dict(raw)
        journal = Journal(ledger.path.parent / "journal" / f"{day.isoformat()}.jsonl")
        strategy = RecommendationStrategy(cfg, lambda t: holder["live"].recs(t), _AtrView(ctx),
                                          journal, run_id, version_info(), "live")
    else:
        raise ValueError(f"paper.yaml strategy must be recommendation, orb or playbook, "
                         f"got {name!r}")
    ledger.start_run(run_id, broker.mode, strategy.name, broker.cfg.starting_capital,
                     strategy.params())
    session = TradingSession(strategy, broker, ledger=ledger, fill_on_bars=False)
    session.start_day(day)
    holder["live"] = LivePaper(session, ctx.feed_store, str(raw.get("mode", "DAY")))
    return holder["live"]
