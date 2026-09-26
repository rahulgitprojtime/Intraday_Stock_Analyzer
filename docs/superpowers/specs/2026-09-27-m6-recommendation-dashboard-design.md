# M6 — Quantitative recommendation MVP: engine, worker, dashboard (design)

Date: 2026-09-27 · Revision 3 · Status: approved; rev 3 = confluence + AVOID handling (DECISIONS #15)

## 1. Goal
Build the first end-to-end MVP of a live/replay intraday stock
recommendation system that ranks liquid Indian equities using deterministic
quantitative analysis, current market context, and explainable setup
detection. The dashboard shows the highest-ranked intraday candidates
according to the configured methodology.

- This is a recommendation / decision-support system.
- It does not place orders and does not automate trading.
- It does not predict or guarantee profits. Scores are ranking scores,
  not probabilities.
- M6 is the first quantitative foundation. Qualitative/news analysis and
  sector context are added in later milestones (M8, M9) without
  redesigning the engine, `Recommendation`, state schema, or dashboard.

## 2. Product boundaries
**In scope:** liquid NSE equity universe · live/replay market data ·
intraday candles · quantitative features · setup detection · in-play
detection · market context · candidate scoring and ranking · explainable
reasons · Streamlit visualization.

**Out of scope:** automated trading · order placement or management ·
portfolio management · paper trading · broker execution · automated
position management · price targets · stop-loss execution · trade alerts ·
investment guarantees.

The application recommends and explains candidates only.

## 3. Architecture
Runtime (unchanged): `worker → engine → state.json → dashboard`.

Conceptual pipeline (M6):
```
Market Data (ReplaySource | LiveSource)
  ↓ Data Quality        (freshness, completeness → data_quality)
  ↓ Universe / Liquidity (series/MIS filter + liquidity eligibility)
  ↓ Feature Engine      (src/indicators, daily_prep)
  ↓ Setup Detection     (src/quantitative/setups.py)
  ↓ In-Play Detection   (src/quantitative/in_play.py)
  ↓ Market Context      (src/market/context.py — NIFTY in M6)
  ↓ Recommendation Engine (src/recommendation/engine.py, pure)
  ↓ Recommendation State (data/processed/state.json)
  ↓ Dashboard           (Streamlit, reads state only)
```
Future extension (not built in M6; interfaces accept it):
```
Recommendation Engine + Sector Context (M8) + Qualitative/Catalyst (M9)
  ↓ Final Recommendation
```
The engine takes a list of **score components**; a future milestone adds
a component (e.g. `sector_context`) plus its configured weight — no engine
API change.

## 4. Quantitative inputs (groups)
Features are organized in groups. M6 implements what is marked ✓; the rest
are defined slots that stay `null` until built.

| Group | Inputs | M6 |
|---|---|---|
| A. Liquidity | avg daily volume ✓, avg traded value ✓, current traded value ✓, spread (needs depth, M7), eligibility ✓ | partial |
| B. Volume | time-of-day RVOL ✓, volume acceleration, volume expansion | RVOL |
| C. Trend | VWAP ✓, EMA9/20 ✓, EMA50, ADX | via setups |
| D. Momentum | price momentum ✓ (momentum burst, RS), RSI, momentum acceleration | via setups |
| E. Volatility | ATR% ✓, intraday range ✓, range expansion ✓, volatility expansion | ✓ |
| F. Price structure | opening range ✓, PDH ✓, day high/low ✓, VWAP relationship ✓, breakout structure ✓ | via setups |
| G. Relative strength | stock vs NIFTY ✓, stock vs sector (M8) | NIFTY only |

Group sub-scores live in `Recommendation.quantitative` (§8). M6 populates
`volume_score`, `volatility_score`, `relative_strength_score` from the
existing in-play components (same computations, not new heuristics) and
`liquidity_score` from §12. `trend_score` and `momentum_score` are `null`
in M6 (their evidence enters via setup states) — not fabricated.

## 5. Setups
Unchanged families and state model (TRIGGERED / FORMING / EXTENDED /
FAILED / NONE; DECISIONS #13).

| Mode | Candles | Setups |
|---|---|---|
| SCALP | 1-min | ORB5, VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK, MOMENTUM_BURST, RS_VS_NIFTY |
| DAY | 5-min | ORB15, PDH, VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK, NARROW_CPR, GAP_AND_GO, RS_VS_NIFTY |

Setup families should remain small and interpretable. New setups require
evidence that they add distinct information.

`ext` = `ext_atr_mult[mode] × prep.atr` (SCALP 0.15, DAY 0.30); if ATR is
unavailable, `ext` = 0.5% of prior close.

## 6. In-play
Definition: whether the stock currently exhibits sufficient market activity
to justify consideration as an intraday candidate. Configurable evidence
(M5): relative volume, gap/price movement, range expansion, ATR%, relative
strength; liquidity is a separate gate (§12).

In-play is not "good setup". The engine records a `profile`:
- `IN_PLAY_STRONG_SETUP` — in play, best setup TRIGGERED/FORMING
- `IN_PLAY_WEAK_SETUP` — in play, best setup NONE/EXTENDED/FAILED
- `NOT_IN_PLAY_SETUP` — not in play, but a setup is TRIGGERED/FORMING
- `NOT_IN_PLAY` — neither

Setups run on every eligible, fresh symbol (not only in-play ones) so the
third profile is observable. Not-in-play symbols are forced to AVOID (§13).

## 7. Scoring — M6 baseline quantitative scoring model
Structure: the final score is a weighted blend of **components**, each
`{name, value 0-100 | null, status: available|unavailable, weight}`, then
adjustments and penalties.

| Component | M6 | Source |
|---|---|---|
| `setup` | ✓ | setup score (below) |
| `in_play` | ✓ | M5 in-play score |
| `market_context` | ✓ | §10 |
| `liquidity` | reported, weight not configured in M6 | §12 |
| `sector_context` | unavailable | M8 |
| `qualitative` | unavailable | M9 |

Setup score: points of the best setup — TRIGGERED 100, FORMING 60,
EXTENDED 30, FAILED/NONE 0.

Confluence bonus (rev 3): additive, applied after the blend and before the
time-of-day caps. Counts **independent setup families** that are
TRIGGERED or FORMING — price_structure (ORB5/ORB15/PDH/GAP_AND_GO/
NARROW_CPR), vwap (VWAP_RECLAIM/VWAP_PULLBACK), trend (EMA_PULLBACK),
momentum (MOMENTUM_BURST), relative_strength (RS_VS_NIFTY). Correlated
setups in one family count once. Volume, market context and in-play are
not counted (already weighted in the blend). 0–1 families +0, 2 → +2,
3 → +3, 4+ → +5; max +5; recorded as a `bonus` adjustment with a reason.

Baseline weights: setup 0.55, in_play 0.35, market_context 0.10. These
weights are temporary engineering defaults and are NOT claimed to be
optimal. They are config (`strategy.yaml` `engine.weights`), not an
architectural assumption. No weights are configured for future components.

## 8. Recommendation object
```
Recommendation
  symbol, mode, as_of
  rank, previous_rank, rank_change, score_change, time_in_top_n   (§16)
  score: float 0-100 | null (null when excluded, §17)
  category: STRONG_CANDIDATE|CANDIDATE|WATCH|NEUTRAL|AVOID | null
  profile: §6
  components: [{name, value, status, weight}]                      (§7)
  quantitative:
    setup_score, in_play_score, is_in_play,
    liquidity_score, momentum_score, trend_score, volume_score,
    volatility_score, relative_strength_score                      (null = unavailable)
    in_play_features: {rvol, gap_pct, atr_pct, range_expansion, rs_pct}
    liquidity: {eligible, avg_daily_volume, avg_traded_value,
                current_traded_value, spread_pct}
  setup: {best, best_state, confluence_count,
          signals: [{name, state, detail}]}
  market_context: {status, score, source: "NIFTY", nifty_change_pct}
  sector_context: {status: "unavailable", sector, sector_score,
                   sector_relative_strength, sector_market_alignment}
  qualitative:    {status: "unavailable"}
  adjustments: [{name, kind: cap|penalty, points, reason}]
  reasons: [{kind, text, evidence}]                                (§18)
  data_quality: {status: OK|STALE|INCOMPLETE, market_data_timestamp,
                 data_age_seconds, stale, missing_inputs: [str]}
```
Unavailable fields are `null` / `status: "unavailable"` — never fabricated.
There are no price fields anywhere in the object.

## 9. Qualitative future compatibility
Qualitative analysis is introduced in a later milestone (M9). M6 must not
assume that quantitative score equals final recommendation quality.
Future qualitative data may include relevant news, company announcements,
earnings, corporate actions, regulatory developments, sector developments,
macro events, catalyst relevance, recency and strength. The qualitative
engine must produce structured, sourced data (DECISIONS #8, CLAUDE.md),
not a free-form LLM opinion. It plugs in as a component + the
`qualitative` block; M6 keeps both.

## 10. Market context
`market_context_score`: M6 uses NIFTY as the initial market-context
input — NIFTY % change since open, linear ramp −0.5% → 0, +0.5% → 100
(clipped). A temporary baseline. If NIFTY data is missing or stale the
component is `unavailable` and excluded from the blend (§13); no neutral
default is invented. Future inputs: BANKNIFTY, market breadth, volatility
regime (INDIAVIX), sector indices. Lives in `src/market/context.py`.

## 11. Sector context
Placeholder `{sector, sector_score, sector_relative_strength,
sector_market_alignment}`. M6: `status: "unavailable"`, all `null`. No fake
sector scores. M8 populates it.

## 12. Liquidity
Uses existing `universe.yaml` `filters` (min_price, min_avg_daily_volume,
min_avg_traded_value, max_spread_pct), which DECISIONS/TODO already
earmarked for M6.
- `avg_daily_volume`, `avg_traded_value` (Σ close×volume per day of 1-min
  candles, averaged over prep sessions), `current_traded_value` (today so
  far) — from the history `build_prep` already fetches; `PrepResult` gains
  these fields (targeted change in `src/data/prep_builder.py`).
- `eligible` = price ≥ min_price, avg volume ≥ min, avg traded value ≥ min.
  Spread check is skipped (depth arrives in M7); `spread_pct: null`.
- Ineligible symbols are excluded from ranking (listed with reason), so an
  illiquid stock cannot rank highly on a strong pattern.
- `liquidity_score` (reported, not blended in M6): linear ramp of
  log10(avg traded value) from the configured minimum (0) to 10× it (100).
  Missing history → `null`, status unavailable.

## 13. Final score formula
```
available = [c for c in components if c.status == available and c.weight is configured]
blend     = Σ(c.weight × c.value) / Σ(c.weight)   over available
score     = clip(blend + confluence_bonus + penalties, 0, 100), then caps (§15)
```
With M6 config, available = setup, in_play, market_context → the 55/35/10
baseline. M6 is intentionally a simplified baseline. Future:
`Quantitative + Market Context + Sector Context + Qualitative + Liquidity
− Penalties`, weights set when each component ships — not hard-coded now.
Adding an unavailable component never changes the score.

Categories: `strategy.yaml` `recommendation.categories` (80 / 65 / 50 /
35; below 35 AVOID).

Hard exclusions vs soft penalties (rev 3):
- **Excluded, not scored** — stale critical data, missing/invalid critical
  inputs, fails liquidity. `excluded: [{symbol, reason}]`; never scored as
  if live (§17).
- **AVOID, scored but not rankable** — not in play, best setup FAILED,
  score below the NEUTRAL floor. Keeps its raw score; `category: AVOID`,
  `eligible_for_top_n: false`, `rank: null`, `exclusion_reasons`.
- **Soft penalties, still rankable** — time-of-day heuristics, EXTENDED
  setups (30 points), future weak-sector/volatility penalties.

Flow: all eligible candidates → score/category → set AVOID aside →
rank the rest → Top-N. An AVOID with a raw score of 95 never outranks a
CANDIDATE at 85.

## 14. Score interpretation
Scores rank candidates; they are not probabilities of profit. UI and state
text must never say "X% chance of profit", "guaranteed", "high probability
of winning", "expected return", "guaranteed upside" or similar, unless a
future research milestone statistically validates such a claim. A test
scans reason/banner strings for these phrases.

## 15. Initial time-of-day heuristics
- before 09:20 → cap at WATCH (score ≤ 64.99)
- 11:30 ≤ t < 13:30 → −10 penalty
- DAY mode, t ≥ 14:45 → cap at WATCH
- t ≥ 15:00 → cap at NEUTRAL (score ≤ 49.99)

These are not validated assumptions. M10 must evaluate whether they
improve ranking quality using historical data. Recorded in `adjustments`.

## 16. Recommendation stability
Fields `rank`, `previous_rank`, `rank_change`, `score_change`,
`time_in_top_n`. M6 sets `rank` for non-AVOID candidates only (score desc,
then symbol asc — deterministic); AVOID gets `rank: null`. The rest stay
`null`. A later milestone maintains
rank history.

## 17. Data freshness
Per symbol: `market_data_timestamp` (last complete 1-min candle),
`data_age_seconds` = as_of − that bar's close time, `stale` if >
`candles.stale_after_seconds` (120, `settings.yaml`; distinct from
`feed.stale_data_threshold_seconds`, which is for live ticks in M7), `missing_inputs` (e.g.
`prep`, `volume_curve`, `index`). Stale or missing critical inputs
(candles, prep) → `data_quality.status` STALE/INCOMPLETE and the symbol is
excluded from ranking. Never scored as if live. Non-critical gaps (e.g.
no volume curve → RVOL unavailable) downgrade: the affected component is
unavailable and listed in `missing_inputs`. Top-level state carries the
feed's overall `data_age_seconds`.

## 18. Reasons / explainability
`reasons: [{kind, text, evidence}]`, `kind` ∈ setup, volume, trend,
momentum, market_context, liquidity, penalty. Only for available
components. Each reason is generated from computed values and carries
them in `evidence` (ratios, percents, states — no price levels), e.g.
`{"kind": "setup", "text": "Reclaimed VWAP with 2.4x time-of-day volume",
"evidence": {"setup": "VWAP_RECLAIM", "state": "TRIGGERED", "rvol": 2.4}}`.
No generic text like "Strong technical setup". A test checks every reason
has non-empty evidence whose keys exist in the recommendation.

## 19. Dashboard — `app/dashboard.py` + `app/view_model.py`
- Reads only `state.json`; never imports `src/broker` or `src/data`.
- Purpose: who are the strongest candidates right now, and why?
- Main table "Top intraday candidates": Rank · Symbol · Category · Score
  · Best Setup · Setup State · RVOL · In Play · Market Context. No extra
  indicator columns.
- Sidebar: Scalp/Day toggle, Top N (5/10/20), category multiselect
  (default excludes NEUTRAL/AVOID), min score.
- Banners: DEMO; stale (state older than 120 s); missing/corrupt state;
  excluded-symbol count; fixed disclaimer "Scores rank candidates; not a
  probability of profit. Not investment advice. No orders are placed."
- Auto-refresh every 30 s (`st.fragment(run_every=30)`).
- `view_model.py` is stdlib: load/validate, filter, sort, top-N, format.

## 20. Candidate card
Category, score, setup chips with states, in-play information and
component bars, reasons, data-quality note, "unavailable" labels for
sector/qualitative. No buy/sell buttons, order form, automated entry or
execution, target or stop fields. It must not resemble an order ticket.

## 21. No price levels
No price levels on recommendation cards (DECISIONS #11). Intentional: this
is a recommendation dashboard, not an execution terminal.

## 22. Replay source and testing hierarchy
`ReplaySource(dir, day)` reads per-symbol 1-min CSVs (the
`IntradayCandleCache` format) and exposes candles strictly up to a
simulated clock (starts 09:16, +1 min per tick; `--speed 0` = no delay).
`LiveSource(adapter, cache)`: GrowwAdapter + incremental cache; tested with
the fake Groww client, run once credentials and a live-price subscription
exist.

ReplaySource is for engineering validation and deterministic development.
Synthetic data must never be treated as evidence that the recommendation
methodology is profitable.

| Data | Use |
|---|---|
| Synthetic | engineering / unit / integration tests |
| Real historical | methodology validation (M10) |
| Replay of real historical sessions | operational validation |
| Live market | production observation |

## 23. Demo data
`scripts/make_demo_data.py` writes ~21 sessions × 10 synthetic symbols +
NIFTY into `data/demo/` (seeded random walk with a few engineered
gap/ORB/volume days); `data/demo/DEMO` marker → `demo: true`. Not
committed. Engineered demo patterns are not strategy evidence; setup rules
are not tuned on synthetic data.

## 24–25. state.json (schema_version 3)
```
{ schema_version: 3, as_of, generated_at, source: "live"|"replay",
  demo, data_age_seconds,
  market: {status, source, nifty_change_pct, score},
  modes: { SCALP: [Recommendation...], DAY: [Recommendation...] },
  excluded: [{symbol, reason}],
  in_play_count, universe_count, errors: [str] }
```
Each mode lists ranked candidates first (by rank), then AVOID candidates
(rank null, by raw score) for transparency. Recommendations carry
`eligible_for_top_n`, `exclusion_reasons`, and `setup.confluence_families`
/ `confluence_bonus`. Written atomically (temp file + replace). v3
replaced v2 because AVOID rank became null (incompatible for readers
sorting by rank); v2 replaced the unreleased v1 draft. Schema changes are logged in
DECISIONS.md; any incompatible change increments `schema_version`, and
`view_model` rejects unknown versions with a clear banner.

## 26. Testing (TDD)
- Engine: state points, confluence by family (+0/+2/+3/+5, cap, correlated
  setups count once, changes the final score), AVOID never ranked and a
  lower non-AVOID outranks a higher AVOID, hard exclusions vs soft
  penalties, blend over available components,
  **unavailable future component leaves score unchanged**, market
  unavailable excluded, each time heuristic, forced AVOID, category
  boundaries, score always 0–100, weight validation (positive, M6 sum 1),
  profiles, deterministic rank ordering incl. ties.
- Schema: required fields present, future fields present as
  null/unavailable, no qualitative/sector values fabricated, no price
  fields (key-name scan), banned-phrase scan (§14).
- Reasons: every reason has kind + evidence traceable to recommendation
  values.
- Data quality: stale symbol excluded; missing volume curve → RVOL
  unavailable and listed.
- Liquidity: eligibility thresholds; ineligible excluded; score ramp;
  missing history → null.
- Worker: ReplaySource never returns candles after the clock; two runs of
  the same replay produce identical state (minus `generated_at`); one tick
  writes schema-valid state; failing symbol reported, not fatal;
  LiveSource with fake client calls only read methods (no execution
  methods exist on the adapter — existing guard test).
- view_model: filters, top-N, staleness, corrupt file, unknown version.
- Dashboard: manual smoke run once pandas/pyarrow are unblocked.

## 27. Validation (M10, not built in M6)
M10 = historical methodology validation: ranking stability, hit rate of
candidate direction, return distribution after recommendation, drawdown,
regime sensitivity, transaction-cost sensitivity where appropriate,
time-of-day effectiveness, feature contribution, setup effectiveness. Do
not optimize solely for maximum historical return. No predictive claims
without out-of-sample validation.

## 28. Out of scope for M6
Live feed/depth, sector context, news/qualitative, weight validation,
alerts, long-term history — none are part of the M6 product.

## 29. Roadmap
- M6 Quantitative recommendation MVP
- M7 Live Groww feed + deeper live market data
- M8 Sector + market breadth + relative-strength context
- M9 Qualitative/news/catalyst engine
- M10 Historical validation and weight/feature evaluation
- M11 Streamlit refinement
- M12 Performance/operational hardening

## 30. Acceptance criteria
1. ReplaySource produces deterministic candles.
2. RecommendationEngine is pure and deterministic.
3. Engine produces ranked candidates.
4. Engine produces explainable, evidence-backed reasons.
5. Engine rejects stale/invalid critical data.
6. Engine does not use future candles.
7. Worker writes schema-valid state.json.
8. Dashboard consumes only state.json.
9. Dashboard displays ranked candidates.
10. No order execution exists in the recommendation path.
11. No price levels are required or shown.
12. Scores are explicitly rankings, not probabilities.
13. Recommendation schema supports future qualitative/sector fields.
14. Synthetic data is clearly labeled DEMO.
15. Tests pass.
16. Existing repository architecture remains intact unless a documented
    reason requires change.

## 31. Development constraints (token efficiency)
Read PROJECT_STATE.md, then this spec; inspect only relevant files; no
full-repo dumps; no repeated code; minimal targeted changes; update
project state after implementation; no future milestones; no unrelated
refactors; tests over long explanations. Progress reports use
Status / Changed / Tests / Next, concisely.

## 32. Config changes (M6)
- `strategy.yaml`: add `engine:` (weights setup/in_play/market_context,
  state points, confluence bonus/cap, market ramp, `ext_atr_mult`, time
  heuristics). Remove the unused `recommendation.weights` and
  `scoring.weights` blocks (they pre-assign future weights, contradicting
  §13); their sum tests become a weight-validation test on
  `engine.weights`. Keep `recommendation.categories`; drop the unused
  `avoid_max_liquidity_score` (liquidity is an eligibility gate).
- `settings.yaml`: `candles.stale_after_seconds: 120`.
- `src/utils/config.py` `load_strategy` validates `engine.weights`
  instead of the removed blocks.
- `ARCHITECTURE.md`: dashboard path `app/dashboard.py` (was `app.py`),
  CSV cache (DECISIONS #12, was parquet), `src/market/context.py`.
