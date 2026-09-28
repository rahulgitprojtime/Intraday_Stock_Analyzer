# TODO.md

Actionable tasks only. Scope: LONG-only upward-momentum candidates;
scalp (1-min) + day (5/15-min) modes (DECISIONS.md #11).

## Done: M0 Foundation ✅ · M1 Groww adapter ✅ · M2 Instrument universe ✅
- [x] Live smoke test of M1 adapter with real credentials (7/7, 2026-09-27)
- [x] `scripts/fetch_replay_data.py`: download last N real 1-min sessions (universe + NIFTY) into replay layout (`data/replay`, gitignored)
- [x] Real-day replay of 2026-09-25 (26/26 symbols x 21 sessions; full day, schema-valid, 0 errors). Finding: not-in-play forced AVOID emptied the list → WATCH cap (DECISIONS #16)
- [ ] Break ties among WATCH-capped names (e.g. by pre-cap score) if the dashboard shows many ties

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
- [x] Dashboard smoke run (AppTest headless + live server) after pandas/pyarrow unblocked
- [x] Confluence by setup family (max +5); AVOID excluded from ranking; schema v3 (DECISIONS #15)

## M7 — Live feed + depth ✅ (spec docs/superpowers/specs/2026-09-28-m7-live-feed-design.md, DECISIONS #17)
- [x] LiveFeed (callback tick counts + 1 s poller), FeedStore, microstructure, watchdog (tick age + stock coverage), spread gate, SCALP component, schema v4, dashboard feed status
- [x] Live-verified 2026-09-28: 25 stocks + NIFTY, 24/24 SCALP with spread/imbalance/velocity, LIVE, 0 restarts
- [ ] Observe a full live session (open → close): watchdog restarts on a real disconnect, REST refresh latency (~5-20 s/minute for 25 stocks)
- [ ] Pin dependency versions (growwapi 1.5.0, nats-py) now that live runs work

## M8 — Sector funnel + prerequisites checklist ✅ (DECISIONS #18)
- [x] sectors.yaml + sector verdicts (index vs NIFTY + peer breadth), sector_context component
- [x] Weak sector: cap at WATCH + penalty after caps; tie-break by pre-cap score
- [x] Prerequisites checklist + summary per stock; dashboard Sector/Prerequisites columns + card checklist; schema v5
- [x] Real replay 2026-09-25 with 9 sector indices: CONFIRMED lead, WEAK deprioritized
- [ ] Verify NIFTYCDTY membership for RELIANCE/ONGC/NTPC/POWERGRID/ULTRACEMCO/ADANIENT
- [ ] Later: exchange-wide breadth, INDIAVIX regime (not needed by the funnel)
## M9 — News check in the funnel (structured, sourced only)
- [ ] Fill the `news` prerequisite (currently NOT_CHECKED) from a real, linked source; no source → NO_RELEVANT_INFORMATION, never invented
## M10 — Historical validation and weight/feature evaluation
## M11 — Streamlit refinement · M12 — Performance/operational hardening
