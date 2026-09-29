# ARCHITECTURE.md

Recommendation-only product (DECISIONS.md #8). Nothing here places orders.

## Data flow

```
Groww API (growwapi SDK + public instrument.csv)
        v
src/broker/         BrokerAdapter + GrowwAdapter + InstrumentMaster
                    <- ONLY module aware of Groww; market data only
        v
src/data/           models, universe resolution, live feed worker,
                    candle engine (1-min historical base + feed LTP) -> 3/5/15m
        v
src/indicators/     pure indicator math (EMA, VWAP, RSI, ATR, ADX, RVOL, ...)
src/quantitative/   features -> quantitative score (deterministic)
src/market/         market context (NIFTY in M6); regime + sector strength (M8)
src/qualitative/    Google News RSS -> deterministic headline-context rules
                    (sourced headlines only; else NO_RELEVANT_INFORMATION)
        v
src/recommendation/ component blend -> time heuristics -> category -> rank ->
                    evidence-backed reasons + prerequisites; state.json (v5)
        v
src/storage/        intraday 1-min CSV candle cache (DECISIONS #12)
src/app/            worker: ReplaySource | LiveSource -> engine -> state.json
        v
app/dashboard.py    Streamlit <- reads data/processed/state.json only
                    (app/view_model.py: stdlib, tested)
src/paper/          SIMULATION ONLY (DECISIONS #20, #29): Strategy -> Broker
                    interface -> SimulatedBroker (fills.py rules, costs.py,
                    limits, 15:15 square-off) -> SQLite ledger.
                    BacktestBroker (cached bars, backtest.py) | PaperBroker
                    (live ticks, src/app/paper_live.py in the worker).
                    TradingSession drives either on a minute clock.
                    strategies/: orb.py, recommendation.py (M10 journal).
                    Never imports src/broker (tested)
app/pages/          Paper trading page <- reads data/paper/*.sqlite only
```

Runtime split: a **live data worker** process owns the Groww feed and REST
polling and writes feature/recommendation state; Streamlit only reads it.

## Hard boundaries

- Only `src/broker/` imports `growwapi` or parses Groww formats.
- Streamlit never holds the feed connection or calls broker methods.
- No LLM computes any number; Python does all indicator/score math.
- Stale critical data => the stock is not recommended.
- `src/paper/` must not reimplement indicator/scoring logic and never sees
  a bar after the current tick. Its orders only ever reach a simulated
  broker; every `Broker` implementation is simulated (tested).

## Config

- `settings.yaml` — storage, instrument cache, feed, candle timeframes.
- `strategy.yaml` — indicator params, in-play scanner, M6 `engine:` baseline
  weights + time heuristics, category thresholds.
- `universe.yaml` — exchange, allowed series, symbols, indices, liquidity filters.
