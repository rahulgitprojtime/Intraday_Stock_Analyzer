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
## M9 — News check in the funnel ✅ (DECISIONS #19)
- [x] Google News RSS source, headline-context rules, per-stock verdict, staggered service, engine effects, checklist + News column + linked headlines
- [x] Live-checked on all 25 stocks; rules hardened (look-alikes, case, clauses, word forms)
- [ ] Optional: keyed source with snippets/sentiment (Marketaux or Drishti) behind the same NewsSource
- [ ] Review misclassified headlines weekly; extend config/news.yaml
## M10 — Paper trading & outcome evaluation (DECISIONS #20)
### M10a ✅ replay simulation
- [x] Entry policy, fixed stop/target, simulator (fills, exits, costs, missed signals), append-only journal, metrics, daily report, replay runner
- [x] Tests: policy, risk, exits incl. same-bar ambiguity, accounting, journal immutability, determinism, look-ahead poison, order safety
- [x] First real run 2026-09-25: NO TRADES (max score 64.99, all capped at WATCH)
### M10b — more data + multi-day evaluation
- [x] Fetched 243 sessions (2025-10-03..2026-09-28) of 1-min history for 35 symbols -> data/replay_1y
- [ ] Run paper_replay --all on data/replay_1y (curated-25 baseline; survivorship caveat)
- [ ] Multi-day aggregate report (equity curve, drawdown, breakdowns with sample sizes); dashboard PAPER TRADING / SIMULATION + M10 Evaluation sections
### M10c — experiments + live paper
- [ ] Baseline vs experiment (config overrides), chronological train/validation/test, walk-forward; component ablation
- [ ] Live paper trading on the running worker (paper.yaml enabled), after smoke tests + replay checks
## M11 — Streamlit refinement · M12 — Performance/operational hardening

## M11 — Market-wide volume scan ✅ (DECISIONS #21)
- [x] Daily stats for all 1,643 NSE EQ intraday stocks, liquid pool, 200/min quote sweep, volume change vs market curve, long-only top 25, dynamic universe in the live worker, scan log
- [ ] First live session with the scan (start worker by ~09:00 for the stats prep)
- [ ] Sector map for scanned stocks (currently only the curated names)
- [ ] Paper trading on the scanned universe uses data/scans history (forward test)

## M13 — Fast scan cycle ✅ (DECISIONS #23)
- [x] Batch OHLC/LTP pre-rank of the pool, parallel quotes for the top movers, shared limiter, stale-quote expiry, SDK timeout
- [ ] Live-check one cycle's duration and call count at the open

## M14 — Research loop ✅ (DECISIONS #24, pre-registered)
- [x] Snapshot recorder, outcome labeller, evaluator (10 questions), worker wiring, scripts, Task Scheduler
- [x] Dry run on 2026-09-25 replay (4,116 rows, 4,020 labeled)
- [x] Historical run over data/replay_1y (m15, 223 days); EXPLORE report reports/replay_explore_m15.md (2026-09-28):
  no signal clears the 0.1% round-trip cost; significant but tiny: 65-79 > 50-64 (+0.02-0.04% at 30/60m),
  confluence 2/3+ > 1, RS_VS_NIFTY and VWAP_RECLAIM > no setup; no evidence for RVOL, sector, 2% move,
  lunch penalty; 80+ too rare (172 rows). Hypotheses only until the validate split.
- [ ] Decide what to test on the validate split (one pre-stated change per run)
- [ ] 2026-09-29 first live research day: check logs/worker_*.log at open, reports/live_2026-09-29.md after 15:45
- [ ] After >= 20 live days: live report; proposed changes tested on validate then test split

## M16 — Whole-market history + price band ✅ build (DECISIONS #26)
- [x] Price band 250..2500 (scan pool, ranking, liquidity gate)
- [x] Daily-candle store, per-day pools (prior sessions only), ReplayScanner, worker --scan-universe, downloader
- [x] Downloaded data/universe_1y (737 instruments, 238 sessions, 3.2 GB); whole-market replay 218 days;
  EXPLORE report reports/universe_explore_m15.md (2026-09-29, 537k panel rows, 155 days):
  final score, RVOL, volume, momentum and movement all rank NEGATIVELY with forward excess return
  within the day (IC -0.01..-0.03, |t| 4-8): among the day's movers the most extended slightly
  mean-revert. RVOL top vs bottom quintile: FINDING worse. EMA_PULLBACK and GAP_AND_GO beat no
  setup (+0.01..0.04%). Sector, confluence, score bands, 2% move, lunch: no evidence. Nothing
  clears the 0.1% cost. Hypotheses only until the validate split.
- [ ] Sector map for scanned stocks (NSE index constituents)

## M17 — Validation tests (DECISIONS #27) ✅ run once 2026-09-29
- [x] T1 reweight away from extension: FAIL (better, still negative IC); T2 lunch penalty: REMOVE; T3 pullback list: FAIL, not tradeable after cost
- [ ] T2 on the test split (once), then strategy m16 without the lunch penalty if it holds
- [ ] New explore hypotheses: the top-50 movers mean-revert; what predicts continuation? (explore split only)

## Live ops — 2026-09-29 first live research day
- [x] Worker (08:40 task) killed at ~09:12 with 0xC000013A (console close / Ctrl+C) — the PowerShell wrapper died too, so not a Python crash; cause unknown. Restarted 09:17:48. Feed errors (NATS "Error:" with empty message) logged just before.
- [x] Task Scheduler: worker trigger repeats every 5 min 08:40-15:25 with IgnoreNew → relaunch within 5 min if it dies
- [ ] Find the cause of the 0xC000013A kills (09:12 and 09:28, both right after a NATS feed disconnect "nats: unexpected EOF"); scheduler relaunched within ~1-2 min
- [ ] Worker must survive network loss: 12:24 DNS failure (getaddrinfo) crashed it (exit 1) and every 5-min relaunch failed at authentication until 13:45 — retry auth/REST with backoff instead of exiting
- [ ] 13:45 laptop hit critical battery and slept until 21:42: 15:45 label task missed (run manually 21:44: 6,365 rows, 6,199 labeled). Keep the laptop plugged in on market days
- Day 1 recorded 09:20-12:24 only (with a 09:28-09:31 gap); strategy m16
- [ ] AARTIPHARM prep failed on a truncated Groww JSON response — retry prep on JSON errors?

## Paper trading + backtesting (DECISIONS #29) ✅ build, branch feature/paper-backtest-broker
- [x] Broker interface; BacktestBroker + PaperBroker (simulated only); fills, Groww cost model, limits, 15:15 square-off
- [x] SQLite ledger, TradingSession, Strategy base, performance metrics, backtest runner + cache/fetch-once
- [x] ORB sample strategy; RecommendationStrategy replaces simulator.py; worker --paper; dashboard Paper trading page
- [ ] First live `--paper` session: check tick fills, ledger growth, dashboard refresh
- [ ] Restore open simulated positions after a worker restart (currently a new run starts)
- [ ] Paper fills for symbols without live ticks (fall back to bars when a symbol has no feed)
- [ ] Run paper_replay --all on data/replay_1y with the new broker (M10b baseline)

## Intraday playbook (DECISIONS #30) — branch feature/paper-backtest-broker
- [x] Opening shortlist (turnover, |gap|, relative volume → top 15) + 5 long setups + article risk rules; backtest `--setups`; live paper `strategy: playbook`
- [ ] Get INDIAVIX 1-min history into the cache so the ORB VIX filter is backtested
- [ ] Sector momentum in the shortlist (needs sector map in the strategy context)
- [ ] Backtest on the whole-market history (data/universe_1y) instead of the curated 25 (survivorship)
