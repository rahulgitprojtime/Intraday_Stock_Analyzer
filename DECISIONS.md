# DECISIONS.md

Append-only log of decisions that would otherwise get re-litigated or
silently drift. Newest at the bottom. Each entry: what was decided, why,
and what it rules out.

---

### #1 — Groww docs verified via web search, not assumed (2026-09-23)
Fetched https://groww.in/trade-api/docs/python-sdk and its Live Data,
Historical Data, and Feed subpages directly rather than relying on training
data, per the project's hard rule against fabricating API behavior. Notes
captured in `docs/groww_api_notes.md`. Anything not in that file should be
re-verified against the live docs before being relied on, since API surfaces
change.

### #2 — No dependency versions frozen in M0 (2026-09-23)
`pyproject.toml` lists dependency *families* (pandas, numpy, streamlit,
growwapi, pyotp, pyarrow, duckdb, pytest) without exact pins. Rationale:
pinning now, before M1 confirms which `growwapi` SDK version behaviors we
depend on, risks locking to a version that's already stale. Pin exact
versions at the end of M1 once the adapter is implemented and tested, and
record the pinned versions here.

### #3 — Storage: start with Parquet + SQLite, not a database service
(2026-09-23)
Per Phase 16 ("start simple"), historical candle cache uses Parquet files on
disk (`data/processed/`) and SQLite for anything needing querying (e.g.
signal history for backtest review). No Redis/Postgres until a concrete
scaling problem appears — if one does, it gets justified here before being
added.

### #4 — Broker adapter is the only Groww-aware module (2026-09-23)
`src/broker/base.py` defines `BrokerAdapter`, an abstract interface. Every
other layer (candles, indicators, signals, risk, UI) depends only on the
dataclasses in `src/data/models.py`, never on Groww-specific types or the
`growwapi` package directly. This is what makes "add another broker" (Phase
20) realistic later without a rewrite, and it's the one abstraction Phase 21
"don't over-engineer" is *not* meant to discourage — it's load-bearing for
Phase 3's explicit requirement.

### #5 — Modes default to non-executing (2026-09-23)
`config/settings.yaml` sets `mode: DATA_ONLY` by default and a separate
`live_trading: false` flag that must be explicitly and manually set true.
Two independent switches (mode + live_trading flag) rather than one, so a
config typo can't silently enable order execution.

### #6 — F&O is out of scope; cash equity only (2026-09-23, user-confirmed)
Derivatives (F&O) trading is explicitly excluded. This project targets NSE/BSE
cash-equity intraday trading only. Concretely this means:
- `Segment` (src/data/models.py) has only `CASH` — no `FNO` member at all.
- `get_option_chain` / `get_greeks` (documented in `docs/groww_api_notes.md`
  for completeness, since they're part of the verified Groww surface) will
  **not** be wrapped in `BrokerAdapter` — there is no F&O-shaped method on
  the interface to implement.
- Universe filters, liquidity scoring, and the signal/risk engines assume
  cash equity throughout (no open-interest, Greeks, or expiry handling
  anywhere in the design).
- If F&O is ever added later, it should be a new segment + new adapter
  methods added deliberately, not a "generalize now for something we might
  need" abstraction — consistent with Phase 21's "don't over-engineer."

### #7 — Hand-rolled retry helper instead of `tenacity` (2026-09-23)
`src/utils/retry.py` is ~30 lines: retry N times with exponential backoff,
only for specified exception types. `tenacity` was in the original M0
dependency list but got removed — the adapter's actual need is small enough
that pulling in a dependency for it fails "never add dependencies without
justification." Revisit only if retry needs grow more elaborate (jitter,
per-call budgets, circuit breaking).

### #8 — Pivot: recommendation engine, never an execution system (2026-09-25, user directive)
The product is a live intraday *recommendation* dashboard, not a trading
bot. Removed: `DATA_ONLY/…/LIVE_TRADING` modes, `live_trading_confirmed`,
risk/position-sizing config, `src/risk/`, and the paper-trading/risk
milestones. Supersedes #5. `BrokerAdapter` exposes market data only; a test
asserts it has no order/position/holdings methods. Backtesting survives
only as *methodology validation* (M10), not a user-facing module. Package
renames (all were empty): `signals/`→`recommendation/`,
`features/`→`quantitative/`; added `qualitative/`, `storage/`.

### #9 — Live feed carries price only; volume comes from REST (2026-09-25)
Verified against current docs: `GrowwFeed` LTP payload is only
`{tsInMillis, ltp}` per exchange_token — no volume, OHLC, or cumulative
quantity. Index feed is `{tsInMillis, value}`; depth feed is
`{tsInMillis, buyBook, sellBook}`. Consequences: tick-built candles would
have no volume, so RVOL/VWAP/OBV must use Groww's **1-minute historical
candles** (which include volume) as the base candle source, refreshed
incrementally each minute within the Live Data rate limit (10/s, 300/min).
The feed is used for freshness (latest LTP, stale detection), the
still-forming candle's price, index values, and top-of-book spread. M3/M4
must re-check whether `get_quote` volume is a cheaper per-minute source.

### #10 — Instrument master via stdlib csv, cached daily (2026-09-25)
Groww's public `instrument.csv` (~20 MB, mostly F&O) is downloaded with
`urllib`, cached at `data/cache/groww_instruments.csv`, refreshed after
20h, and filtered to CASH EQ/IDX rows (~12.7k). No SDK auth needed for
this. If a refresh fails, the stale cache is used (tokens rarely change
intraday). Stock sector is **not** in the CSV — M7 needs a sector map.

### #11 — Long-only momentum setups; dashboard before feed (2026-09-25, user-confirmed)
- LONG/upward-momentum candidates only. No short setups for now.
- Ranking is driven by named, deterministic setups experienced intraday
  traders use (ORB, VWAP pullback/reclaim, PDH breakout, narrow CPR,
  gap-and-go, EMA9/20 pullback, RS vs NIFTY, 1-min momentum burst), gated
  by a stocks-in-play filter (time-of-day RVOL, gap, ATR%) and time-of-day
  rules. Two modes: Scalp (1-min) and Day (5/15-min).
- Cards show no price levels (no trigger/invalidation/stop/target).
- Build order: REST 1-min pipeline → setups → scanner → recommendation +
  dashboard MVP → live feed/depth (for Scalp quality) → context → news.
- "Scalp" means 1-minute candidates; Groww is not a low-latency source.

### #12 — Daily candles derived from 1-min history; CSV candle cache (2026-09-25)
One 1-min history request per stock (29 calendar days, inside the 30-day
window) gives both daily OHLCV (aggregated locally) for CPR/NR7/ATR and the
20-session volume-by-minute curve. Avoids the unverified Groww daily-interval
constant and halves REST calls. The intraday cache is stdlib CSV
(`src/storage/candle_cache.py`, atomic replace), not parquet: Windows
Application Control blocks pandas/pyarrow DLLs on the dev machine. Revisit
if that changes or the cache becomes a bottleneck.

### #13 — Setup detector conventions (2026-09-27)
`src/quantitative/setups.py`: each detector takes today's candles of one
timeframe, ignores forming bars, and returns `SetupSignal(name, state,
detail)` with state NONE/FORMING/TRIGGERED/EXTENDED/FAILED. `ext` (distance
past the trigger that counts as EXTENDED) is passed by the caller, derived
from ATR in M6. FORMING = within 0.75% below the trigger; pullback touch
tolerance 0.1%; narrow CPR <= 0.25% width; gap-and-go >= 1% gap (failed on
gap fill); RS trigger >= 0.5 pts vs NIFTY since open; momentum burst =
body and volume >= 2x the prior 20-bar average, close in top quarter.
All illustrative, not tuned — M10 validates. PDH breakout requires a
cross; opening above PDH is gap-and-go territory, not PDH.

### #14 — M6 extensible recommendation model; state schema v2 (2026-09-27, user-reviewed)
Spec: `docs/superpowers/specs/2026-09-27-m6-recommendation-dashboard-design.md`.
- Final score = weighted blend over *available* score components
  (`setup`, `in_play`, `market_context` in M6; 0.55/0.35/0.10 is a
  temporary baseline, not optimal). Unavailable components (sector M8,
  qualitative M9) are excluded, never defaulted — adding one never changes
  the score. Future weights are set when a component ships; the old
  pre-assigned `scoring.weights` / `recommendation.weights` blocks are removed.
- Liquidity (universe.yaml filters) is an eligibility gate; ineligible,
  stale or incomplete symbols are excluded from ranking, never scored as live.
- `Recommendation` carries quantitative group sub-scores, market/sector/
  qualitative blocks, adjustments, evidence-backed reasons, data_quality and
  rank-stability fields; `null` = unavailable. No price fields.
- Missing NIFTY → market_context unavailable (no neutral 50).
- `state.json` schema_version 2 (v1 was an unreleased draft). Incompatible
  changes increment the version and are logged here.
- Time-of-day rules and all thresholds are unvalidated heuristics until M10.

### #15 — Confluence by setup family; AVOID not rankable; state schema v3 (2026-09-27, user-approved)
- Confluence bonus counts independent setup *families* (price_structure,
  vwap, trend, momentum, relative_strength) that are TRIGGERED/FORMING:
  0–1 → +0, 2 → +2, 3 → +3, 4+ → +5 (max 5), after the blend, before
  time caps. Correlated setups in a family count once; volume/market/
  in-play are not counted again (already in the blend). Replaces the
  rev-2 `min(100, best + 10k)` rule, which could never change a score.
- Two hard tiers: *excluded* (stale/missing critical data, illiquid —
  not scored) and *AVOID* (not in play, best setup failed, score below
  NEUTRAL floor — scored, `eligible_for_top_n: false`, `rank: null`,
  `exclusion_reasons`). Soft penalties (time-of-day, EXTENDED, future
  sector/volatility) stay rankable. Top-N is drawn from rankable only.
- `state.json` schema_version 3 (AVOID rank null breaks v2 readers).

### #16 — Not in play caps at WATCH instead of forcing AVOID (2026-09-28, user-approved)
- First real-day replay (2026-09-25, 25 large caps): time-of-day RVOL
  rarely reaches `min_rvol` 1.5, so "not in play → AVOID" left 0
  rankable stocks on 62 of 75 sampled ticks despite TRIGGERED setups.
- Now not-in-play is a soft cap: score capped just below the CANDIDATE
  floor (≤ WATCH), stays rankable; in-play names always outrank it.
  Hard AVOID gates remain: best setup FAILED, score below NEUTRAL floor.
- Known trade-off: capped names tie at the cap and sort by symbol.
  Supersedes the "not in play" item of #15. Schema unchanged (v3).

### #17 — M7 live feed: hybrid read path, spread gate, SCALP microstructure; schema v4 (2026-09-28, user-approved)
- Spec: `docs/superpowers/specs/2026-09-28-m7-live-feed-design.md`.
- The SDK callback gets only meta, so `LiveFeed` counts ticks in the
  callback (exact velocity) and a 1 s poller reads latest LTP/depth into a
  thread-safe `FeedStore` (`src/data/`). Ages use local arrival time.
- Health by tick age (`FeedWatchdog`): STALE 30 s, DOWN 60 s → restart with
  a fresh socket token, max 1/min, backoff to 5 min after 5. The worker
  never stops for the feed: REST candles keep ranking. Health also needs
  stock coverage >= 50% (stocks ticking within 60 s): live, after a network
  drop, NIFTY kept ticking while 23/24 stocks went silent.
- Spread > `max_spread_pct` (0.5) excludes a stock (both modes) when depth
  is fresh; otherwise "spread unchecked". `microstructure` component
  (imbalance 60% / velocity 40%) is SCALP-only at weight 0.10; engine
  weights 0.50/0.30/0.10/0.10. DAY/replay renormalize over the rest
  (0.556/0.333/0.111) — small shift, user-accepted.
- Velocity is None for the first 5 min a symbol is observed (the 5-min
  average is understated during warm-up; seen live as 5.0 for everything).
- `BrokerAdapter` streaming stubs removed; streaming lives in
  `src/broker/groww_feed.py`; `GrowwAdapter.api_client()` hands the SDK
  client to it. State schema v4 adds a top-level `feed` block.

### #18 — M8 sector funnel + prerequisites checklist; schema v5 (2026-09-28, user-approved design, no spec by request)
- User intent: weak sector → deprioritize; good candidate → confirm the
  sector (other stocks in it moving) → technicals → top 10; news in
  parallel (M9). Each listed stock shows what was checked and how it came
  out. Approach "funnel stages with soft effects + checklist" chosen over
  a hard gate (would empty the list on quiet days) and a pure blend.
- `config/sectors.yaml` (user-maintained; CSV has no sector): 9 sectors
  mapped to Groww NSE indices; LT, BHARTIARTL, TITAN have none → sector
  UNAVAILABLE, still eligible, flagged (user choice). NIFTYCDTY
  membership is best-effort.
- Verdict (`src/market/sector.py`): rs = sector index % since open − NIFTY's;
  peers = *other* universe members up since open. CONFIRMED: rs ≥ +0.2
  and (< 2 usable peers or ≥ 60% up); WEAK: rs ≤ −0.2 or (≥ 2 peers and
  ≤ 40% up); stale/missing → UNAVAILABLE (never guessed, no cap).
- Scoring: `sector_context` component 0.10 (both modes); weights setup
  0.45 / in_play 0.30 / market 0.05 / microstructure 0.10 / sector 0.10.
  WEAK → capped at WATCH **and** −5 after caps. Replay 2026-09-25 showed
  why: every stock was already at the not-in-play cap, so a cap alone
  changed nothing and ties sorted alphabetically (WEAK AXISBANK #1).
  Ranking ties now break by pre-cap score, then symbol (supersedes the
  #16 trade-off).
- `prerequisites` (technicals, in play, liquidity, sector, market, news)
  with PASS/WARN/FAIL/NA/NOT_CHECKED + detail, and a one-line
  `prerequisites_summary`; news NOT_CHECKED until M9. Schema v5.
- Out of scope: exchange-wide breadth, INDIAVIX, NSE-sourced membership.

### #19 — M9 news check: Google News RSS + headline-context rules (2026-09-28, user-approved, no spec by request)
- Probed 2026-09-28: NSE and BSE announcement APIs return 403 to scripts
  (not bypassed). Google News RSS works, no key, but carries the
  **headline only** (description repeats it; links are Google redirects).
  User chose RSS now, pluggable `NewsSource` for a keyed API later
  (Marketaux / Drishti give snippets + sentiment; free tiers are small).
- Rules (`src/qualitative/headline_rules.py`, `config/news.yaml`), user
  asked to read headlines in context: stock named (aliases; common-word
  aliases case-sensitive; look-alike companies excluded) → not a
  roundup/list/price page → not speculation → direction phrase nearest
  the stock, same clause (`;`/`|`), within 8 words; `*` gaps allowed
  ("cuts * target"); negation within 5 words before neutralises.
- Per stock (`news_check.py`): 18 h look-back, same story across outlets
  merged (word overlap ≥ 60% of the shorter headline, same direction),
  weighted by outlets → POSITIVE / NEGATIVE / MIXED / NEUTRAL /
  NO_RELEVANT_INFORMATION. Stale (> 30 min) or failed → UNAVAILABLE.
- Effects: POSITIVE +3 before caps (credibility, never enough alone);
  NEGATIVE capped at WATCH and −5 after caps (like a weak sector).
  Checklist news line cites the headline, outlet(s) and time; cards link
  the real headlines. Replay: news NOT_CHECKED (no historical news).
- Fetching staggered (3 stocks/minute, each ~10 min) inside the worker
  tick; never blocks ranking. Live check on today's headlines found and
  fixed look-alike and word-form misses; rules remain illustrative and
  unvalidated (M10). Known gap: republished old stories (e.g. "Q1 results"
  in September) can't be told apart from a headline alone.

### #20 — M10 paper trading / outcome evaluation, simulation only (2026-09-28, user-approved)
- Narrows #8: simulated positions are allowed **for evaluating the
  recommendation methodology**. Still no order/position/holdings API, no
  order-style UI, no trading modes (a single `paper.yaml enabled: false`
  switch; LIVE trading does not exist). `src/paper` never imports
  `src/broker` (tested).
- #11 still holds for recommendation cards (no levels). Stop/target exist
  only in the paper journal and the separately labelled simulation views.
- Conventions (config/paper.yaml, unoptimised starting values): entry =
  existing recommendation with category ≥ CANDIDATE, score ≥ 65, best setup
  TRIGGERED, top 10, max 3 open, 1 trade/symbol/day, none after 15:00;
  fill at the next bar's OPEN + 5 bps; stop = fill − 0.25 × daily ATR (0.6%
  fallback), target = 1.5R, both fixed at entry; gap below stop fills at the
  open; stop and target in one bar → STOP_LOSS; 15:20 square-off at the last
  closed bar's close; 0.05% round-trip charges; 10 shares fixed.
- Journal: append-only JSONL (ENTRY / EXIT / MISSED); entries immutable;
  trade id = hash(run, day, symbol, mode, signal time, strategy_version);
  every record carries strategy_version, config hash, git commit.
- Replay: the simulator runs on the worker's clock with bars closed ≤ T
  only; a poison test proves later bars cannot change earlier decisions or
  fills. Historical news does not exist → NOT_AVAILABLE, never backfilled.
- First real run (2026-09-25, default policy): NO TRADES — max score all
  day 64.99; every stock capped at WATCH (not in play, #16). Recorded as a
  finding; the policy was not loosened to manufacture trades.
- Known biases stated in reports/docs: survivorship (today's 25 large
  caps), no historical spread/depth, small samples (< 30 → warning).

### #21 — M11 market-wide volume scan picks the live universe (2026-09-28, user directive)
- User: "full market scan; don't limit to a few stocks"; then "pick the top
  25 by highest change in volume via Groww API, then rank them by our
  parameters". The hand-written 25-stock list now serves only replays and
  scan-disabled runs; live, the universe is chosen each minute.
- Verified live 2026-09-28: 1,643 NSE EQ-series stocks allow intraday (of
  4,292 cash instruments). `get_quote` carries today's cumulative volume
  (~194 sequential calls/min); `get_ohlc` (50/call) has no volume; the
  feed has no volume (#9). Daily candles: one call, ≤ 180 days.
- Pipeline: daily candles for all 1,643 (cached per day, ~8 min) → 20-day
  stats → liquid pool (existing filters) → background quote sweep at
  200 calls/min (Groww Live Data cap 300/min shared with the worker) →
  volume change = today's volume ÷ (20-day avg × market share of a normal
  day traded by now) → long-only (above previous close, user choice a) →
  top 25 (min stay 10 min; pinned symbols never drop) → existing M4-M9
  funnel ranks them → dashboard top 10.
- The time-of-day share is a market-wide curve measured from 6,025 real
  stock-days (config/market_volume_curve.json; 7% by 09:30, 39% by noon,
  81% by 15:00), rebuilt with scripts/build_volume_curve.py.
- New members: existing prep (one 1-min history call), feed resubscribed
  on membership change, news uses curated aliases else the Groww name.
  Sector map still covers only the curated names → others "not available".
- Every tick's scan is appended to data/scans/<date>.jsonl, building a
  point-in-time history for M10 (a historical market-wide backtest would
  need ~21k calls / ~9 GB of 1-min data; deferred).
- State schema v6: top-level `universe` block (source, pool, quoted,
  sweeps, active symbols with volume change).

### #22 — M12 one transparent score from eight groups (2026-09-28, user directive)
- User: "whichever stocks make the most score out of these indicators rank
  on top; simple" — criteria grouped as PRICE MOVEMENT, VOLUME, MOMENTUM,
  SETUP, MARKET, SECTOR, LIQUIDITY/DATA, QUALITATIVE.
- `src/quantitative/groups.py`: each group 0-100 from sub-signals (averaged
  over those with data; none → the group drops out, never counted as 0).
  Group key for price movement is `movement` (the word "price" is banned in
  output keys so no price levels leak, #11). ADX counts only above EMA20
  (it is direction-agnostic; long-only). RSI > 80 = overextended → 50.
- Final score = weighted average of available groups, user-chosen weights:
  setup 20, volume 20, movement 15, momentum 15, sector 10, market 10,
  liquidity 5, news 5. Supersedes the special caps of #16 (not in play →
  WATCH), #18 (weak sector → WATCH − 5) and #19 (news +3 / cap − 5): those
  signals now just lower their group. Kept: time-of-day rules (user
  choice), hard rejects (stale/invalid data, illiquid, spread > 0.5%,
  failed setup, below NEUTRAL floor).
- Two stages because full criteria need 1-min candles (one call per stock
  per minute; Groww cap 300/min): stage 1 pre-ranks the whole liquid pool
  from quotes on the quote-computable groups (movement, volume incl.
  acceleration between sweeps, liquidity; same weights, normalised) → top
  50 → stage 2 scores all eight groups each minute.
- BANK NIFTY joins the market group for sectors listed in
  `sectors.yaml bank_nifty_sectors`; market regime = NIFTY vs its EMA20.
- Weights are a judgement call, unvalidated; change only via M10 experiments.

### #23 — M13 fast scan cycle (2026-09-28)
- The M11/M12 sweep quoted the pool one stock at a time (~200/min), so a
  full pass took several minutes and rankings mixed stale quotes.
- Now once a minute: day OHLC for the whole liquid pool in batches of 50;
  prices from the live feed where subscribed, batch LTP for the rest;
  movement pre-rank of every pool stock (long-only: above previous close);
  then parallel quotes (4 workers, shared limiter 8 calls/s < Groww 10/s)
  for only the top `movers_per_cycle` (100) movers, which carry volume.
- Quotes older than 180 s drop out of the ranking. Every SDK call gets a
  10 s timeout (growwapi defaults to none; a stalled call froze the scan).
- Budget per minute ≈ pool/50 OHLC + pool/50 LTP + 100 quotes + worker
  1-min candles for the active set — under the 300/min cap.
- `scan_now.py --save` writes the top symbols; `fetch_replay_data.py
  --symbols-file` downloads replay data for them.

### #24 — M14 research loop: snapshot → outcome → evaluation (2026-09-28, user directive; PRE-REGISTERED)
- User: answers must come "from your data, not from assumptions about what
  experts use". Committed BEFORE any result was seen.
- Snapshots (`src/research/snapshots.py`): every scored stock every 5 min
  live (15 min for the historical replay) + an event row when a stock moves
  up into WATCH or better. Flat rows: group scores, sub-signals, raw %
  values (day change, VWAP distance, ROC, RSI, ADX, volume acceleration),
  setup states, confluence, sector, news, microstructure, time rules,
  pre-rule score, strategy version + config hash + git commit. No prices.
- Outcomes (`src/research/outcomes.py`): entry = open of the snapshot
  minute's bar (scored only on closed bars, so no look-ahead); exit = close
  after 5/15/30/60 min; MFE/MAE; excess vs NIFTY and the sector index;
  net of an assumed 0.1% round trip. Windows past 15:25 are truncated
  (left empty), missing bars → None.
- Evaluation (`src/research/evaluate.py`), fixed rules: panel rows only;
  metric = excess return vs NIFTY; 95% intervals by day-resampling
  bootstrap; < 30 rows or < 20 days → INSUFFICIENT; FINDING only if the
  interval excludes 0 at ≥ 2 horizons with one sign and the sign holds in
  both chronological halves; else NO EVIDENCE.
- The ten questions (report sections): (1) RVOL quintiles + within-movement
  rank correlation; (2) sector CONFIRMED vs WEAK beyond technicals
  (outcome minus same technical-score decile mean); (3) news POSITIVE vs
  NO_RELEVANT_INFORMATION beyond technicals (live only); (4) confluence 3+
  and 2 vs 1; (5) score bands 80+ vs 65-79 vs 50-64, per mode; (6) day
  change buckets 2-3% and > 3% vs 1-2% (no explicit 2% rule exists; the
  closest are the DAY momentum ROC ramp and the 0-3% movement ramp);
  (7) SCALP microstructure top vs bottom tercile, 5/15 min (live only);
  (8) lunch 11:30-13:30 vs neighbouring hours at the same pre-penalty
  score ≥ 50 — the penalty is justified only if lunch is worse; (9) each
  setup TRIGGERED vs no setup; (10) group correlation matrix + each
  group's own rank correlation (redundant = corr > 0.7 and no separate value).
- Anything else noticed is a hypothesis for later, not a finding.
- Splits (chronological, never shuffled): history data/replay_1y —
  explore 2025-11-03..2026-06-30, validate 2026-07-01..2026-08-31, test
  2026-09-01..2026-09-28 (used once per proposed change). Live snapshots
  from 2026-09-29 are a separate forward test on the scanned universe.
- A change = new strategy_version, compared with the current one on the
  held-out period; weights are never fitted on the whole dataset.
- Strategy version bumped to 2026-09-29.m14 (M12/M13 changed scoring).
- Operations: Windows Task Scheduler, weekdays 08:40 worker, 15:45 label +
  reports (`scripts/live_day.ps1`, logs/). Runs only while the user is
  logged on; the PC must be on.
- Biases stated in every use: history = today's 25 large caps
  (survivorship); no historical news/depth; one day is description, not
  evidence.

### #25 — News unweighted; setup 20% → 25% (2026-09-28, user directive)
- User: "remove news weightage for now; replace it with setup (20% -> 25%).
  I will google myself for any news for the stocks you display."
- Groww's API has no news endpoint (checked growwapi 1.5.0 methods, feed
  topics, docs and changelog 2026-09-28), so there is no better source to
  weight yet.
- Weights: setup 25, volume 20, movement 15, momentum 15, sector 10,
  market 10, liquidity 5; news none. The news check still runs live and
  shows on the card checklist and in snapshots (unweighted), so research
  question 3 (does news add information beyond technicals) can still be
  answered from data.
- Strategy version 2026-09-29.m15. Research rows record the version; the
  historical replay made under m14 is not mixed with m15 results.

### #26 — M16 whole-market history; price band 250..2500 (2026-09-28, user directive)
- User: "limiting to these 25 stocks is a big mistake, we need to consider
  the entire universe for intraday"; then "consider price >= 250 and <= 2500
  for now, leave the rest".
- Price band applies everywhere: live scan pool (previous close), each
  minute's ranking (last price), and the engine's liquidity gate ("price
  above maximum"). Other filters unchanged (avg volume ≥ 5 lakh, avg
  traded value ≥ ₹5 cr). Live pool on 2026-09-28: 625 → 337 stocks.
  Curated names outside the band now fail the gate in replays.
- History (`scripts/fetch_universe_history.py`, data/universe_1y):
  daily candles for all 1,643 NSE EQ intraday stocks → per-day pool from
  the 20 prior sessions only (`pools.json`) → 1-min bars for every stock
  ever in a pool + NIFTY, BANK NIFTY, sector indices. Paced 150 calls/min,
  resumable, run outside market hours.
- Replay (`worker --replay DIR --scan-universe`): `ReplayScanner` builds
  each minute's quotes from bars closed by that minute and feeds the live
  `rank_volume_change` → `ActiveSet` (top 50, 10-min stay) →
  `DynamicUniverse` → engine. Same selection code as live.
- Differences stated in reports: history knows every pool stock's volume
  each minute (live quotes volume for the top 100 movers only); today's
  instrument list (delisted stocks missing); sector map covers only the
  curated names (sector group drops out for the rest, never 0).
- Research rules of #24 unchanged; the whole-market results go to
  data/research/universe, the 25-stock results stay for comparison.
