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
- [ ] Worker loop wiring (universe → prep at start, refresh each minute) — lands with M6 worker

## M4 — Indicators + long setup detectors
- [ ] EMA9/20/50, VWAP, RSI, MACD, ADX, Supertrend(10,3), ATR, ROC, time-of-day RVOL
- [ ] Setups (state FORMING/TRIGGERED/EXTENDED/FAILED): ORB 5/15 breakout, VWAP pullback/reclaim, PDH breakout, narrow-CPR trend day, gap-and-go, EMA9/20 pullback, RS vs NIFTY, 1-min momentum burst
- [ ] Reference-value tests per indicator; fixture-candle tests per setup

## M5 — Stocks-in-play scanner
- [ ] RVOL, gap %, ATR%, range expansion, relative strength → in-play score

## M6 — Recommendation engine + dashboard (MVP)
- [ ] Score = best setup + in-play + context; categories; time-of-day rules; explanations
- [ ] Worker writes state each minute; Streamlit reads it (top 5/10/20, candidate card, Scalp/Day toggle, filters). No price levels on cards.

## M7 — Live feed + depth (unlocks Scalp mode quality)
- [ ] GrowwFeed wrapper: LTP/index/depth for top ~20; dedupe, reconnect, stale detection; tick velocity, spread, bid/ask imbalance

## M8 — Market + sector context (regime, sector map, sector RS)
## M9 — Qualitative/news engine (sourced only; low weight in Scalp mode)
## M10 — Validation: per-setup historical hit rate by time of day → weights
## M11 — Performance · M12 — Final testing/security
