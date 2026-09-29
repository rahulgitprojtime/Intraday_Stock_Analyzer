# PROJECT_STATE.md

Last updated: 2026-09-28

## Product
Live intraday **recommendation** dashboard (NSE cash equities). Groww is a
market-data source only; the app never places orders (DECISIONS.md #8).

## Scope (2026-09-25, DECISIONS.md #11)
LONG-only upward-momentum candidates. Ranking driven by named setups used
by intraday traders (ORB, VWAP, PDH, CPR, EMA pullback, momentum burst),
Scalp (1-min) and Day (5/15-min) modes, stocks-in-play pre-filter,
time-of-day rules. No price levels on cards. Dashboard MVP before live feed.

## Current milestone: M14 ✅ (research loop, DECISIONS #24) → first live research day 2026-09-29

### Completed
- **M0** foundation: layered architecture, config, data models, `BrokerAdapter`.
- **M1** `src/broker/groww.py`: both auth flows, quote/LTP/OHLC (chunked 50),
  historical candles (window-split per interval), exception translation,
  retry/backoff. Mock-tested only; never run against a real account.
- **M2** `src/broker/groww_instruments.py` (`InstrumentMaster`: download,
  daily disk cache, stale-cache fallback, CASH EQ/IDX index) and
  `src/data/universe.py` (`resolve_universe`: series filter, dedupe, max
  size, indices, explicit rejection reasons). `GrowwAdapter(master)` returns
  instruments with `exchange_token`, name, series, is_index. Checked against
  the live CSV: all 25 starter stocks + 9 indices resolve.
- **Pivot cleanup (2026-09-25)**: removed trading modes, risk config and
  `src/risk/`; renamed empty packages to `quantitative/`, `recommendation/`;
  added `qualitative/`, `storage/`; recommendation weights/categories in
  `strategy.yaml`; a test guards against order methods on the interface.
- **M3a** `Instrument.is_intraday` parsed from the CSV; `require_intraday`
  in universe.yaml rejects non-MIS stocks. `src/quantitative/daily_prep.py`:
  `compute_daily_prep` (prior H/L/C, CPR normalized top>=bottom + width %,
  NR7, inside day, Wilder ATR/ATR%) and `avg_cumulative_volume_curve`
  (375 session minutes, ffill missing minutes) for time-of-day RVOL.
- **M3b** `src/data/prep_builder.py` `build_prep` (one 1-min request per
  stock → daily candles → DailyPrep + 20-session volume curve; only
  sessions before today). `src/data/candles.py`: `aggregate_daily`,
  `resample` (09:15-aligned, forming bucket flagged), `is_stale`.
  `src/storage/candle_cache.py` `IntradayCandleCache` (CSV per symbol/day,
  incremental refresh re-fetching the last bar). DECISIONS #12. Session
  constants live in `src/data/models.py`.
- **M4a** `src/indicators/core.py`: EMA (SMA seed), Wilder RSI/ATR/ADX,
  MACD, Supertrend(10,3), ROC, session-reset VWAP (typical price),
  time-of-day RVOL. Series outputs aligned to input, `None` until warm.
- **M4b** `src/quantitative/setups.py`: 9 long setup detectors (ORB 5/15,
  PDH breakout, VWAP reclaim, VWAP pullback, EMA9/20 pullback, narrow-CPR
  trend, gap-and-go, RS vs NIFTY, 1-min momentum burst) returning
  `SetupSignal` states. Conventions + default thresholds: DECISIONS #13.
- **M5** `src/quantitative/in_play.py`: `score_in_play` (time-of-day RVOL,
  gap-up %, ATR%, range expansion vs daily ATR, RS vs NIFTY → linear ramps
  → weighted 0-100) with an RVOL floor gate; `rank_in_play`. Weights,
  ramps, `min_score`, `min_rvol` in `strategy.yaml` `in_play:`.
- **M6** (spec `docs/superpowers/specs/2026-09-27-m6-recommendation-dashboard-design.md`,
  DECISIONS #14): liquidity gate (`src/quantitative/liquidity.py`), NIFTY
  market context (`src/market/context.py`), data quality, extensible
  `Recommendation` model, baseline scoring (component blend 0.55/0.35/0.10
  over *available* components, time heuristics, categories), engine
  (`src/recommendation/engine.py`), state.json v2 schema, worker
  (`src/app/worker.py`, ReplaySource/LiveSource), deterministic DEMO data
  (`scripts/make_demo_data.py`), Streamlit dashboard (`app/`). Replay of
  the demo day runs end to end; LiveSource is unrun (no credentials).

- **M7** (spec `docs/superpowers/specs/2026-09-28-m7-live-feed-design.md`,
  DECISIONS #17): `src/broker/groww_feed.py` `LiveFeed` (callback counts
  ticks, 1 s poller reads LTP/depth/NIFTY), `src/data/feed_store.py`,
  `src/quantitative/microstructure.py` (spread, imbalance, velocity with a
  5-min warm-up), `src/app/feed_watchdog.py` (tick age + stock coverage,
  restart with backoff), spread liquidity gate, SCALP-only microstructure
  component (weights 0.50/0.30/0.10/0.10), state schema v4 `feed` block,
  dashboard feed status. `scripts/feed_smoke.py`. Live worker verified
  2026-09-28 (REST + feed): LIVE, 24/24 SCALP with feed metrics.

- **M8** (DECISIONS #18): `config/sectors.yaml`, `src/market/sector.py`
  (CONFIRMED/NEUTRAL/WEAK/UNAVAILABLE from sector index vs NIFTY + peers),
  `sector_context` component, weak sector capped at WATCH and −5 after
  caps, ties broken by pre-cap score, `src/recommendation/prerequisites.py`
  checklist + summary per stock (news NOT_CHECKED until M9), schema v5,
  dashboard Sector/Prerequisites columns and card checklist. Worker loads
  9 sector indices (replay + live); `fetch_replay_data.py` downloads them.

- **M9** (DECISIONS #19): `src/qualitative/` news source (Google News RSS),
  headline-context rules, per-stock verdict, staggered `NewsService`;
  POSITIVE +3 / NEGATIVE capped + −5; checklist news line, News column,
  linked headlines on cards. Live only (replay: NOT_CHECKED).

- **M10a** (DECISIONS #20): `src/paper/` policy, risk, simulator, journal,
  metrics, report; `scripts/paper_replay.py` drives it from the existing
  worker tick. First real run 2026-09-25: NO TRADES (all capped at WATCH).

- **M11** (DECISIONS #21): live universe = top 25 by volume change across
  all 1,643 NSE EQ intraday stocks (`src/app/market_scanner.py`,
  `src/quantitative/volume_scan.py`, `src/app/dynamic_universe.py`,
  `get_daily_candles`, `scripts/scan_now.py`). 12 months of 1-min data for
  the curated 25 + indices in `data/replay_1y` (M10b baseline).

- **M12** (DECISIONS #22): one score from eight groups with user weights;
  two-stage selection (quote pre-rank → top 50 → full score).
- **M13** (DECISIONS #23): fast once-a-minute scan (batch OHLC/LTP for the
  pool, feed prices where subscribed, parallel quotes for the top 100
  movers, shared rate limiter, stale-quote expiry, 10 s SDK timeout).
- **M14** (DECISIONS #24, pre-registered): `src/research/` snapshots,
  forward outcomes, evaluation of the ten questions; worker records live
  snapshots; `scripts/label_outcomes.py`, `scripts/evaluate.py`,
  `scripts/research_replay.py`; Task Scheduler runs `scripts/live_day.ps1`
  weekdays 08:40 (worker) and 15:45 (label + reports/live_<day>.md).
- **M15** (DECISIONS #25): news unweighted, setup 25%.
- **M16** (DECISIONS #26): price band 250..2500; whole-market history
  (`src/research/universe_history.py`, `scripts/fetch_universe_history.py`,
  `worker --replay --scan-universe`).
- **Paper trading + backtesting** (DECISIONS #29, branch
  `feature/paper-backtest-broker`): `src/paper/` `Broker` interface with
  simulated `BacktestBroker`/`PaperBroker` only (no live broker), fill
  rules, Groww cost model (`config/costs.yaml`), long-only limits, 15:15
  square-off, SQLite ledger, `TradingSession`, `Strategy` base,
  performance (win rate, net after costs, max DD, Sharpe, equity curve),
  ORB sample + `RecommendationStrategy` (replaced `simulator.py`),
  `scripts/backtest.py` (cache + fetch-once), worker `--paper`,
  dashboard page `app/pages/1_Paper_trading.py`. ORB on 83 cached days:
  net -29% after costs (unoptimised sample).
- **Intraday playbook** (DECISIONS #30): opening shortlist (turnover,
  |gap|, relative volume → top 15) + ORB / VWAP / EMA 5-15 rejection /
  Bollinger reversal / PDL bounce (long only) with the article's risk
  rules; `scripts/backtest.py --strategy playbook [--setups ...]`; live
  paper `strategy: playbook`.
- **Whole-market setup study, long + short** (DECISIONS #31, pre-registered):
  `src/research/setup_backtest.py` + `scripts/setup_study.py` — ORB / VWAP /
  EMA 5-15 / Bollinger / previous-day level, both sides via price mirroring,
  candle confirmation, 10/30-min + daily trend agreement (MTF), exits
  NATIVE / +1 / +2 / +5 / +10%. Explore (161 sessions, 110k stock-days,
  1.09 M signals): 0 of 100 variants pass after Groww charges; the raw
  edge before costs is ~0. Validate/test not run.
- **User's trading plan** (DECISIONS #32, pre-registered):
  `src/research/plan_backtest.py` + `scripts/plan_study.py` — Rs 4 lakh,
  Rs 80k per trade, 2% trailing stop, +3/4/5% targets, 3-5 trades a day
  with a NIFTY/BANK NIFTY + market-volume gate. Explore: 0 of 24 variants
  pass (all setups -Rs 57k to -80k). Trade-level breakdown
  (`src/research/trade_analysis.py`, `scripts/plan_trade_analysis.py`,
  `docs/research/plan_trade_analysis.md`): costs ~Rs 154/trade vs a
  pre-cost edge of Rs 4 (all signals) to Rs 70 (the plan's picks).

### Tests
554 passing, 1 skipped (2026-09-30, paper/backtest branch; 417 before it) locally (`.venv`, Python 3.13, pytest). `growwapi` is not
installed in the venv; adapter tests use `tests/fakes/fake_groww.py`.
pandas/pyarrow DLLs are blocked by Windows Application Control in this
venv — unblocked 2026-09-27 (pandas 3.0.6, pyarrow 25.0.1, streamlit
1.64.0). Core code stays stdlib-only; only the dashboard uses Streamlit.
Dashboard smoke-tested (AppTest headless + served on a local port).

### Key facts / known issues
- Feed LTP payload has **no volume**, so volume-based features come from
  1-min historical candles (DECISIONS.md #9). Biggest design constraint.
- Sector is not in the instrument CSV; M7 needs a maintained sector map.
- `GrowwFeed` reconnect behavior is undocumented; M3 must handle it
  defensively (tick-age heartbeat, resubscribe on reconnect).
- M1 live-checked 2026-09-27: `scripts/groww_smoke.py` passes 7/7 (auth,
  resolve, LTP, OHLC, quote+depth, 1-min history stock + index). History
  parsing fixed for real payloads (pre/post-session rows, null prices,
  null index volume) — docs/groww_api_notes.md.

### M6 follow-ups (DECISIONS #15, #16, done)
Confluence counts independent setup families (+0/+2/+3/+5, max 5);
AVOID candidates are scored but never ranked; state.json schema v3.

### Next task
Real-day replay done 2026-09-28; not-in-play caps at WATCH (DECISIONS #16).
M7 live-verified; M8 done. Next: M9 — fill the news prerequisite from a
real sourced feed (brainstorm the source first); observe a full live
session.
