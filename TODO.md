# TODO.md

Actionable tasks only. Scope: LONG-only upward-momentum candidates;
scalp (1-min) + day (5/15-min) modes (DECISIONS.md #11).

## Done: M0 Foundation ✅ · M1 Groww adapter ✅ · M2 Instrument universe ✅
- [ ] Live smoke test of M1 adapter with real credentials
- [ ] Pin dependency versions (DECISIONS.md #2) after first real run

## M3 — Daily prep + REST candle pipeline (done)
- [x] Universe: MIS-allowed (`is_intraday=1`) liquid NSE EQ stocks
- [x] Daily prep (`src/quantitative/daily_prep.py`, pure fns): prior-day H/L/C, CPR + width, NR7/inside day, ATR%, 20-day avg volume curve by minute
- [x] M3b: `build_prep` — one 1-min request/stock → daily candles + DailyPrep + volume curve
- [x] Per-minute incremental 1-min refresh, CSV cache (`IntradayCandleCache`, DECISIONS #12)
- [x] Resample 3/5/15-min; incomplete-candle + staleness flags (`src/data/candles.py`)
- [x] Worker loop wiring (universe → prep at start, refresh each minute) — M6 worker

## M4 — Indicators + long setup detectors
- [x] EMA9/20/50, VWAP, RSI, MACD, ADX, Supertrend(10,3), ATR, ROC, time-of-day RVOL
- [x] Setups (state FORMING/TRIGGERED/EXTENDED/FAILED): ORB 5/15 breakout, VWAP pullback/reclaim, PDH breakout, narrow-CPR trend day, gap-and-go, EMA9/20 pullback, RS vs NIFTY, 1-min momentum burst
- [x] Reference-value tests per indicator (`tests/test_indicators.py`)
- [x] Fixture-candle tests per setup (`tests/test_setups.py`)

## M5 — Stocks-in-play scanner
- [x] RVOL, gap %, ATR%, range expansion, relative strength → in-play score (`src/quantitative/in_play.py`)

## M6 — Quantitative recommendation MVP (spec: docs/superpowers/specs/2026-09-27-m6-recommendation-dashboard-design.md)
- [x] Spec revision 2 (extensible model, schema v2) — approved
- [x] Engine (components blend, setup score, time heuristics, categories, profiles, reasons, data quality)
- [x] Liquidity eligibility + market context (NIFTY)
- [x] Worker: ReplaySource/LiveSource, state.json v2, demo data script
- [x] Streamlit dashboard + view_model (no price levels)
- [ ] Dashboard smoke run once pandas/pyarrow DLLs are unblocked
- [ ] Decide: confluence bonus no-op; forced-AVOID ranking (see PROJECT_STATE)

## M7 — Live feed + depth (unlocks Scalp mode quality)
- [ ] GrowwFeed wrapper: LTP/index/depth for top ~20; dedupe, reconnect, stale detection; tick velocity, spread, bid/ask imbalance

## M8 — Sector + market breadth + relative-strength context
## M9 — Qualitative/news/catalyst engine (structured, sourced only)
## M10 — Historical validation and weight/feature evaluation
## M11 — Streamlit refinement · M12 — Performance/operational hardening
