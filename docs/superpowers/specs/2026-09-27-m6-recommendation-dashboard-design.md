# M6 — Recommendation engine, worker, dashboard (design)

Date: 2026-09-27 · Status: approved in chat, pending spec review

## Goal
First end-to-end MVP: every minute, rank LONG-only intraday candidates
(Scalp and Day modes) and show them on a Streamlit dashboard. Analytical
categories only — never orders, never price levels on cards (DECISIONS #8, #11).

## Constraints
- No Groww live-price subscription or `.env` yet → the live path is built
  but unrun; a **replay** path drives everything now.
- Streamlit needs pandas/pyarrow, currently blocked by Windows Application
  Control; the user will unblock. Core code (`src/`) stays stdlib-only.
- Scores are rankings, not probabilities of profit (said in the UI).

## 1. Recommendation engine — `src/recommendation/engine.py` (pure)

**Modes and setups**

| Mode | Candles | Setups |
|---|---|---|
| SCALP | 1-min | ORB5, VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK, MOMENTUM_BURST, RS_VS_NIFTY |
| DAY | 5-min | ORB15, PDH, VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK, NARROW_CPR, GAP_AND_GO, RS_VS_NIFTY |

`ext` passed to detectors = `ext_atr_mult[mode] × prep.atr`
(SCALP 0.15, DAY 0.30). If `prep.atr` is None, `ext` = 0.5% of prior close.

**Setup score (0-100)**: state points TRIGGERED 100, FORMING 60,
EXTENDED 30, FAILED/NONE 0. `setup = min(100, best + 10 × k)` where `k` =
number of TRIGGERED setups other than the best one, capped at 2.

**Market score (0-100)**: NIFTY % change since open, linear ramp
−0.5% → 0, +0.5% → 100 (clipped). No index data → 50 (neutral).

**Final score**: `0.55 × setup + 0.35 × in_play.score + 0.10 × market`,
then time-of-day rules, then clipped to 0-100.

**Time-of-day rules** (naive IST, evaluated on the as-of minute):
- before 09:20 → cap at WATCH (score min(score, 64.99))
- 11:30 ≤ t < 13:30 → −10
- DAY mode, t ≥ 14:45 → cap at WATCH
- t ≥ 15:00 → cap at NEUTRAL (min(score, 49.99))

**Category**: existing `strategy.yaml` `recommendation.categories`
thresholds (80 / 65 / 50 / 35; below 35 AVOID). Forced AVOID when the stock
is not in play, or when the best setup's state is FAILED.

**Output** `Recommendation`: symbol, mode, score, category, setup_score,
in_play_score, market_score, setups (name, state, detail), reasons
(list of strings: best setup detail, confluence, in-play reason, time rule
applied), as_of. No price fields.

**Config**: new `strategy.yaml` `engine:` section — blend weights
(0.55/0.35/0.10), state points, confluence bonus/cap, market ramp,
`ext_atr_mult`, time rules. The old `recommendation.weights` block
(quant/qual/sector/liquidity) is removed; qualitative and sector inputs
return in M8/M9 and will be re-added then. Categories block stays.
Values are illustrative, not tuned (M10). `test_recommendation_weights_sum_to_one`
is replaced by an equivalent check on `engine` blend weights.

## 2. Worker — `src/app/worker.py`

**Candle source** (small protocol, two implementations):
- `ReplaySource(dir, day)`: reads per-symbol 1-min CSVs (same format as
  `IntradayCandleCache`) for prior days and the replay day; exposes candles
  up to a simulated clock. Clock starts 09:16 and advances one minute per
  tick (`--speed` ticks per real second-scaled delay; `--speed 0` = as fast
  as possible).
- `LiveSource(adapter, cache)`: `GrowwAdapter` + `IntradayCandleCache`
  incremental refresh. Built and unit-tested with the fake Groww client;
  not run until credentials exist.

**Startup**: resolve universe (+ NIFTY index), `build_prep` per symbol
(DailyPrep + volume curve). Symbols whose prep fails are skipped and listed
in state `errors`.

**Each tick** (minute): refresh candles → `score_in_play` for all →
for in-play symbols run each mode's setups (1-min for SCALP, `resample`
5-min for DAY, forming bars excluded) → engine → write
`data/processed/state.json` atomically (temp file + replace).

**state.json** (schema_version 1):
```
{ schema_version, as_of, generated_at, source: "live"|"replay",
  demo: bool, data_age_seconds, market: {nifty_change_pct, score},
  modes: { SCALP: [Recommendation...], DAY: [Recommendation...] },
  in_play_count, universe_count, errors: [str] }
```
Recommendations are sorted by score desc; all scored symbols included
(AVOID too) so the dashboard can filter.

**CLI**: `python -m src.app.worker --replay data/demo --day 2026-09-25
--speed 60` or `python -m src.app.worker` (live). One tick failing logs
the error into state and continues.

**Demo data**: `scripts/make_demo_data.py` writes ~21 sessions × 10
synthetic symbols + NIFTY into `data/demo/` (seeded random walk with a few
engineered gap/ORB/volume days). `data/demo/DEMO` marker → state
`demo: true`. Not committed (generated).

## 3. Dashboard — `app/dashboard.py` (Streamlit) + `app/view_model.py`

- Reads only `state.json`; never imports `src/broker` or `src/data`.
- Sidebar: Scalp/Day toggle, Top N (5/10/20), category multiselect
  (default all except AVOID/NEUTRAL), min score slider.
- Banners: DEMO data; stale if `now − as_of > 120 s`; missing/corrupt
  state file; fixed disclaimer "Scores rank candidates; not a probability
  of profit. Not investment advice. No orders are placed."
- Ranked table (rank, symbol, category, score, best setup, state, RVOL,
  in-play) + one card per row: category badge, score, setup chips with
  states, in-play component bars, reasons. No price levels.
- Auto-refresh every 30 s (`st.fragment(run_every=30)`).
- `view_model.py` (stdlib): load/validate state, filter, sort, top-N,
  formatting — unit-tested without Streamlit.

## Testing
- Engine: TDD — state points, confluence cap, market ramp + missing
  index, each time rule, forced AVOID, category boundaries, no price
  fields in output.
- Worker: ReplaySource clock never leaks future candles; one tick on
  fixture data writes a schema-valid state.json; failing symbol is
  reported, not fatal; LiveSource with fake Groww client.
- view_model: filters/top-N/staleness/corrupt file.
- Dashboard: manual smoke run after pandas/pyarrow are unblocked.

## Out of scope (later milestones)
Live feed/depth (M7), sector context (M8), news (M9), weight validation
(M10), alerts, persistence of history beyond the CSV cache.
