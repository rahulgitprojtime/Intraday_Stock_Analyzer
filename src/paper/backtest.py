"""Backtest runner — DECISIONS #29. SIMULATION ONLY.

Replays cached 1-min bars minute by minute through `TradingSession` +
`BacktestBroker`. Each bar is released at its CLOSE time (open + 1 min), so
a strategy acting on bar T fills at bar T+1's open at the earliest.

Candles come from `IntradayCandleCache` (`<root>/<day>/<SYMBOL>.csv`, the
layout `fetch_replay_data.py` and the worker already use). `ensure_cached`
fills gaps with ONE historical request per symbol (the adapter splits it
into <= 30-day windows) and writes empty files for days with no data
(holidays), so a range is downloaded once.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from src.data.models import SESSION_OPEN, Exchange, HistoricalCandleRequest, Instrument, Segment
from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.orders import Bar
from src.paper.performance import performance
from src.paper.session import TradingSession
from src.paper.strategy import Strategy
from src.storage.candle_cache import IntradayCandleCache

ONE_MIN = timedelta(minutes=1)
SESSION_CLOSE = time(15, 30)


@dataclass
class BacktestResult:
    run_id: str
    strategy: str
    trades: list[dict]
    equity_curve: list[tuple[datetime, float]]
    daily: list[dict]
    metrics: dict
    orders: list = field(default_factory=list)


def weekdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


def ensure_cached(cache: IntradayCandleCache, adapter, inst: Instrument, days: list[date],
                  today: date) -> int:
    """Download missing past `days` for `inst`; returns how many were fetched."""
    missing = [d for d in days if d < today and not cache.path(inst, d).exists()]
    if not missing:
        return 0
    got = adapter.get_historical_candles(HistoricalCandleRequest(
        inst, datetime.combine(missing[0], SESSION_OPEN),
        datetime.combine(missing[-1], SESSION_CLOSE), interval_minutes=1))
    by_day = defaultdict(list)
    for c in got:
        by_day[c.timestamp.date()].append(c)
    for d in missing:
        cache.save(inst, d, by_day.get(d, []))    # empty file = no session (holiday)
    return len(missing)


def load_day(cache: IntradayCandleCache, symbols: Iterable[str], day: date) -> list[Bar]:
    bars = []
    for sym in symbols:
        inst = Instrument(sym, Exchange.NSE, Segment.CASH)
        bars += [Bar.from_candle(c) for c in cache.load(inst, day) if c.is_complete]
    return bars


def run_day(session: TradingSession, day: date, bars: list[Bar]) -> dict:
    by_close: dict[datetime, list[Bar]] = defaultdict(list)
    for b in bars:
        by_close[b.ts + ONE_MIN].append(b)
    session.start_day(day)
    for as_of in sorted(by_close):
        session.step(as_of, by_close[as_of])
    return session.end_day(day)


def run_backtest(strategy: Strategy, days: Iterable[tuple[date, list[Bar]]],
                 cfg: BrokerConfig, costs: CostModel, *, ledger=None,
                 run_id: str = "backtest") -> BacktestResult:
    """`days`: (day, that day's 1-min bars for every symbol), in date order."""
    if ledger is not None:
        ledger.start_run(run_id, "BACKTEST", strategy.name, cfg.starting_capital,
                         strategy.params())
    broker = BacktestBroker(cfg, costs, ledger, run_id)
    session = TradingSession(strategy, broker, ledger=ledger, fill_on_bars=True)
    for day, bars in days:
        if bars:
            run_day(session, day, bars)
    if ledger is not None:
        ledger.commit()
    return BacktestResult(run_id, strategy.name, broker.trades, session.equity_curve,
                          session.daily,
                          performance(broker.trades, session.equity_curve, session.daily,
                                      cfg.starting_capital), broker.orders())
