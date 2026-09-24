# TODO.md

Actionable tasks only. Detail lives in `PROJECT_STATE.md` / `DECISIONS.md`.

## Done: M0 Foundation ✅ · M1 Groww adapter ✅ · M2 Instrument universe ✅
- [ ] Live smoke test of M1 adapter + `get_historical_candles` with real credentials
- [ ] Pin dependency versions (DECISIONS.md #2) after first real run

## M3 — Live feed (next)
- [ ] `GrowwFeed` wrapper in `src/broker/`: subscribe/unsubscribe LTP, index value, depth by exchange_token (≤1000)
- [ ] Normalize `tsInMillis` payloads to models; drop duplicate/out-of-order ticks
- [ ] Connection monitoring, reconnect w/ backoff, per-symbol stale detection
- [ ] Live data worker process (not Streamlit) writing latest-tick state
- [ ] Check whether `get_quote` volume is a viable per-minute volume source (DECISIONS.md #9)

## M4 — Candle engine
- [ ] 1-min base from Groww historical candles (incremental); forming candle from feed LTP
- [ ] Resample 3/5/15-min; market open/close, gaps, incomplete-candle flags

## M5 — Quantitative features
- [ ] EMA9/20/50, VWAP, ADX, Supertrend, RSI, MACD, ROC, ATR, BB width, OBV
- [ ] Time-of-day-adjusted RVOL (cumulative volume at T vs historical avg at T)
- [ ] PDH/PDL, day H/L, opening range, breakout/breakdown, VWAP distance
- [ ] Reference-value unit tests per indicator; quantitative sub-score

## M6 — Liquidity / high-volume scanner
- [ ] Apply `universe.yaml` filters (ADV, traded value, price, spread) + liquidity score

## M7 — Market + sector
- [ ] Sector map for universe stocks (not in instrument CSV)
- [ ] Regime classifier + sector relative strength / alignment

## M8 — Qualitative engine
- [ ] Source retrieval (exchange announcements/news), normalization, LLM structuring with citations

## M9 — Recommendation engine
- [ ] `RecommendationEngine`: blend, categories, ranking, explanations, freshness gating

## M10 — Methodology validation (no look-ahead)
## M11 — Streamlit dashboard (top N, candidate card, controls)
## M12 — Performance (incremental recompute, caching)
## M13 — Final testing / security (secret scan, docs)
