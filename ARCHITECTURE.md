# ARCHITECTURE.md

## Data flow

```
Groww API (growwapi SDK)
        |
        v
BrokerAdapter (src/broker/)         <- the ONLY module that imports growwapi
        |
        v
Market Data Layer (src/data/)       <- live feed + historical, normalized
        |                              into src/data/models.py dataclasses
   +----+----+
   |         |
Live Feed  Historical
   |         |
   +----+----+
        v
Candle Engine (src/data/candles.py) <- builds 1/3/5/15-min candles
        v
Feature Engine (src/features/)
        |
   +----+----+
   |         |
Indicators  Market
(src/       Context
indicators/) (src/market/)
   +----+----+
        v
Signal Engine (src/signals/rules.py)
        v
Scoring Engine (src/signals/scoring.py)
        v
Risk Engine (src/risk/)             <- independent of signal generation
        v
Ranking Engine (src/signals/ranking.py)
   +----+----+
   |         |
Backtest   Paper Trading
(src/       (later milestone)
backtest/)
   +----+----+
        v
Streamlit UI (app.py)               <- consumes processed state only,
                                        never owns the market-data connection
```

## Module boundaries (hard rules, not suggestions)

- `src/broker/` is the only place `import growwapi` (or any broker SDK) may
  appear. Everything downstream consumes `src/data/models.py` types.
- `app.py` (Streamlit) may import from `src/signals`, `src/risk`, `src/data`
  for *reading* processed state. It must never hold the primary live-feed
  connection or call broker methods directly — that lives in a background
  process/thread managed by `src/data/feed.py`.
- `src/risk/` never imports from `src/signals/` — risk sizing is computed
  independently and attached to a signal afterward, not baked into signal
  scoring, so a change in one can't silently change the other.
- `src/backtest/` reuses the same feature/signal/scoring code paths as live
  trading (same functions, historical inputs) — it must not reimplement
  indicator logic, or backtest results stop being trustworthy evidence about
  live behavior.

## Config-driven, not code-driven

Anything a user should be able to tune without touching Python lives in
`config/*.yaml`:
- `settings.yaml` — mode, risk limits, storage paths, logging.
- `strategy.yaml` — scoring weights, signal thresholds.
- `universe.yaml` — liquidity filters, symbol universe.

## Why this shape

The spec (Phase 2) asks for strict separation so that (a) the market-data
connection can survive independently of Streamlit reruns, (b) backtesting
can reuse live logic instead of drifting into a separate implementation,
and (c) another AI/developer can add an indicator, strategy, or broker by
touching one module instead of tracing logic through the whole app.
