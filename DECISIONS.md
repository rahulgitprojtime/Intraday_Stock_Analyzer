# DECISIONS.md

Decisions that would otherwise get re-litigated or silently drift. Each
entry states what holds **today**; when a later decision changes an earlier
one, the earlier entry is rewritten to match (cleaned up 2026-10-08), so
nothing here contradicts the code. Numbers are stable because code and
config cite them; append new decisions at the bottom.

Retired numbers: #5 (trading-mode switches; replaced by #8), #16 (not-in-play
cap at WATCH; replaced by the group score of #22).

---

## Product and boundaries

### #1 — Groww behaviour is verified, never assumed (2026-09-23)
Groww SDK behaviour comes from the live docs (groww.in/trade-api/docs) or
live checks, recorded in `docs/groww_api_notes.md`. Anything not in that
file is re-verified before it is relied on.

### #4 — The broker adapter is the only Groww-aware module (2026-09-23)
`src/broker/` (`BrokerAdapter`, `GrowwAdapter`, `InstrumentMaster`,
`groww_feed.py`) is the only code that imports `growwapi` or parses Groww
formats. Every other layer uses the dataclasses in `src/data/models.py`.

### #6 — Cash equity only; F&O out of scope (2026-09-23, user-confirmed)
NSE cash equity, intraday. `Segment` has only `CASH`; option-chain/Greeks
endpoints are not wrapped. Adding F&O would be a deliberate new segment.

### #8 — Market data + recommendations + SIMULATED trading; never live orders (2026-09-25, updated 2026-10-08)
- The product is a live intraday recommendation dashboard plus a paper
  trading/backtesting simulator (#29, #30). No order ever reaches Groww.
- `BrokerAdapter` exposes market data only (a test asserts it has no
  order/position/holdings methods). Orders exist only inside the simulated
  brokers in `src/paper/`, which never import `src/broker` or `growwapi`.
- No trading modes and no live-trading switch exist; the dashboard has no
  order controls. Recommendation categories are analytical, not instructions.

### #7 — Hand-rolled retry helper (2026-09-23)
`src/utils/retry.py` (retry N times, exponential backoff, chosen exception
types) instead of `tenacity`. Revisit only if retry needs grow.

### #2 — Dependencies not pinned yet (2026-09-23)
`pyproject.toml` lists dependency families without versions. Pin
(growwapi 1.5.0, nats-py, …) once live runs are stable; record pins here.

### #3 — Storage: files + SQLite, no database service (2026-09-23, updated 2026-10-08)
1-min candles: stdlib CSV cache (#12). State: `data/processed/state.json`.
Research and scans: JSONL. Paper/backtests: SQLite ledgers (`data/paper/`).
No Redis/Postgres until a concrete need is justified here. All data
directories are gitignored.

## Data

### #9 — Live feed carries price only; volume comes from REST (2026-09-25)
`GrowwFeed` LTP payload is `{tsInMillis, ltp}` (index `{tsInMillis,
value}`, depth `{tsInMillis, buyBook, sellBook}`): no volume. Candles with
volume come from 1-min historical data refreshed each minute; the feed gives
freshness, the latest price, index values and top-of-book depth. `get_quote`
carries today's cumulative volume (used by the scan, #21).

### #10 — Instrument master via stdlib csv, cached daily (2026-09-25)
Groww's public `instrument.csv` is cached at
`data/cache/groww_instruments.csv`, refreshed after 20 h, filtered to CASH
EQ/IDX rows; a failed refresh uses the stale cache. It has no sector column
(sector map: `config/sectors.yaml`, #18).

### #12 — Daily stats from 1-min history; CSV candle cache (2026-09-25)
One 1-min history request per stock (29 calendar days) gives daily OHLCV
(aggregated locally) for CPR/NR7/ATR and the 20-session volume-by-minute
curve. The intraday cache is stdlib CSV with atomic replace: Windows
Application Control blocks pandas/pyarrow DLLs on the dev machine. The
market scan uses Groww daily candles directly (one call, ≤ 180 days, #21).

## Recommendation engine (long only)

### #11 — Long-only momentum candidates; no price levels on cards (2026-09-25, updated 2026-10-08)
- The recommendation engine, its scoring and the research loop look for
  LONG/upward-momentum candidates only. Shorts exist only in the paper
  strategies (#30, #31); a short-side engine would be a new, untested model.
- Named deterministic setups (ORB, VWAP pullback/reclaim, PDH breakout,
  narrow CPR, gap-and-go, EMA9/20 pullback, RS vs NIFTY, 1-min momentum
  burst) plus time-of-day rules. Two modes: SCALP (1-min) and DAY (5/15-min).
- Recommendation cards show no price levels (no trigger/stop/target).
  Stops and targets exist only in the paper simulator and its views.

### #13 — Setup detector conventions (2026-09-27)
`src/quantitative/setups.py`: each detector takes today's candles of one
timeframe, ignores forming bars, returns `SetupSignal(name, state, detail)`
with state NONE/FORMING/TRIGGERED/EXTENDED/FAILED. EXTENDED distance is
ATR-based (`ext_atr_mult`). FORMING = within 0.75% below the trigger;
pullback touch tolerance 0.1%; narrow CPR ≤ 0.25% width; gap-and-go ≥ 1%
gap (fails on gap fill); RS ≥ 0.5 pts vs NIFTY since open; momentum burst =
body and volume ≥ 2x the prior 20-bar average, close in the top quarter.
PDH breakout requires a cross. Illustrative, untuned.

### #14 — Recommendation model and state.json (2026-09-27, updated 2026-10-08)
- A `Recommendation` carries group scores, market/sector/qualitative
  blocks, evidence-backed reasons, prerequisites, data quality and rank
  fields; `null` = unavailable, never defaulted. No price fields.
- Liquidity is an eligibility gate; stale, incomplete or illiquid symbols
  are excluded from ranking, never scored as live.
- `state.json` is versioned (currently schema v6); incompatible changes bump
  the version and are logged here. Weights: #22/#25.

### #15 — Confluence by setup family; AVOID is not rankable (2026-09-27)
- Bonus by independent setup families TRIGGERED/FORMING (price_structure,
  vwap, trend, momentum, relative_strength): 2 → +2, 3 → +3, 4+ → +5.
- Excluded (stale/missing critical data, illiquid, spread > 0.5%) = not
  scored. AVOID (best setup FAILED, score below the NEUTRAL floor) = scored,
  `rank: null`, never in the top N. Time-of-day caps stay rankable.

### #17 — Live feed: hybrid read path, spread gate, SCALP microstructure (2026-09-28)
- `LiveFeed` counts ticks in the SDK callback; a 1 s poller copies latest
  LTP/depth into a thread-safe `FeedStore`. Ages use local arrival time.
- `FeedWatchdog`: STALE 30 s, DOWN 60 s → restart with a fresh token, max
  1/min, back off to 5 min after 5; also DOWN when < 50% of stocks tick.
  The worker never stops for the feed; REST candles keep ranking.
- Spread > 0.5% excludes a stock when depth is fresh. SCALP gets a
  microstructure signal (imbalance 60% / velocity 40%; velocity unknown for
  the first 5 min of a symbol).

### #18 — Sector confirmation + prerequisites checklist (2026-09-28)
- `config/sectors.yaml` (user-maintained) maps sectors to NSE indices;
  unmapped stocks → sector UNAVAILABLE, still eligible.
- Verdict: rs = sector index % since open − NIFTY's; peers = other universe
  members up since open. CONFIRMED / WEAK / UNAVAILABLE (thresholds in
  `strategy.yaml sector`). The verdict feeds the sector group (#22).
- Every card shows a prerequisites checklist (technicals, in play,
  liquidity, sector, market, news: PASS/WARN/FAIL/NA/NOT_CHECKED + detail).

### #19 — News check: Google News RSS + headline rules, unweighted (2026-09-28)
- NSE/BSE announcement APIs refuse scripts; Groww has no news endpoint.
  Google News RSS (headline only) behind a pluggable `NewsSource`.
- Headline-in-context rules (`headline_rules.py`, `config/news.yaml`) give
  POSITIVE / NEGATIVE / MIXED / NEUTRAL / NO_RELEVANT_INFORMATION per stock
  (18 h look-back, stories merged across outlets); stale/failed →
  UNAVAILABLE. Fetched 3 stocks/minute, never blocks ranking.
- News has no weight in the score (#25); it is shown on the checklist with
  linked headlines and recorded for research.

### #22 — One transparent score from groups (2026-09-28, user directive)
- Each group is 0-100 from its sub-signals (averaged over those with data;
  none → the group drops out). Groups: movement (key avoids "price", #11),
  volume, momentum, setup, market, sector, liquidity. ADX counts only above
  EMA20; RSI > 80 = overextended → 50.
- Final score = weighted average of available groups, weights from #25.
  Kept: time-of-day rules, hard rejects (#15). Weak sector, not-in-play and
  news have no special caps; they just score low in their group.
- Two stages: stage 1 pre-ranks the liquid pool from quotes (movement,
  volume incl. acceleration, liquidity) → top 50 → stage 2 scores all groups
  on 1-min candles each minute. BANK NIFTY joins the market group for bank
  sectors; market regime = NIFTY vs its EMA20.

### #25 — Current weights: setup 25, volume 20, movement 15, momentum 15, sector 10, market 10, liquidity 5 (2026-09-28, user directive)
News unweighted (the user checks news by hand). Weights are a judgement
call, unvalidated; they change only through pre-registered tests (#24).
Current `strategy_version` is in `config/strategy.yaml` (2026-09-29.m16:
lunch penalty removed, #27).

## Universe

### #21 — Market-wide volume scan picks the live universe (2026-09-28, user directive)
- All NSE EQ-series intraday stocks (1,643 on 2026-09-28) → daily candles
  (cached per day) → 20-day stats → liquid pool (`universe.yaml filters`:
  price 250-2500 per #26, avg volume ≥ 5 lakh, avg value ≥ ₹5 cr).
- Volume change = today's volume ÷ (20-day avg × the market's normal share
  of the day traded by now; `config/market_volume_curve.json`, from 6,025
  real stock-days, rebuilt by `scripts/build_volume_curve.py`).
- Which stocks, and how many: #31 (market sentiment picks the side; every
  qualifying stock joins; the API budget is the only ceiling). Min stay 10
  min; open paper positions are pinned and never drop.
- Each minute's scan is appended to `data/scans/<date>.jsonl`. The curated
  `universe.yaml symbols` list serves replays and scan-disabled runs only.

### #23 — Fast scan cycle (2026-09-28)
Once a minute: batch OHLC for the pool (50/call), feed prices where
subscribed + batch LTP for the rest, movement pre-rank, then parallel quotes
(4 workers, shared limiter 8 calls/s < Groww's 10/s) for the strongest
movers in the market's direction, as many as the minute's API budget
allows (#31). Quotes older than 180 s drop out. Every SDK call has a 10 s
timeout. Total stays under the 300 calls/min cap.

### #26 — Whole-market history; price band 250..2500 (2026-09-28, user directive)
- The price band applies to the live pool, each minute's ranking and the
  engine's liquidity gate.
- History (`scripts/fetch_universe_history.py` → `data/universe_1y`): per-day
  pools from the 20 prior sessions only (`pools.json`), 1-min bars for every
  stock ever in a pool + NIFTY, BANK NIFTY, sector indices; paced, resumable.
- `worker --replay DIR --scan-universe` replays with the live selection code
  (`ReplayScanner` → `rank_volume_change` → `ActiveSet` → engine).
- Stated differences: history knows every pool stock's volume each minute;
  delisted stocks are missing; the sector map covers only curated names.

## Research (pre-registered; results are records, not to be re-run)

### #24 — Research loop: snapshot → outcome → evaluation (2026-09-28, user directive; PRE-REGISTERED)
- Answers come from data, not assumptions. Rules committed before results.
- Snapshots (`src/research/snapshots.py`): every scored stock every 5 min
  live (15 min in historical replay) + an event row when a stock moves up
  into WATCH or better; group scores, sub-signals, raw % values, setup
  states, sector, news, microstructure, time rules, strategy version +
  config hash + git commit. No prices. Short-only universe names excluded (#30).
- Outcomes (`outcomes.py`): entry = open of the snapshot minute's bar; exit =
  close after 5/15/30/60 min; MFE/MAE; excess vs NIFTY and sector; net of a
  0.1% round trip; windows past 15:25 truncated.
- Evaluation (`evaluate.py`): panel rows; metric = excess return vs NIFTY;
  95% day-bootstrap intervals; < 30 rows or < 20 days → INSUFFICIENT;
  FINDING only if the interval excludes 0 at ≥ 2 horizons with one sign and
  the sign holds in both chronological halves; else NO EVIDENCE.
- Ten questions: RVOL, sector beyond technicals, news beyond technicals,
  confluence, score bands, day-change buckets, SCALP microstructure, lunch,
  each setup vs none, group correlations.
- Splits (chronological): explore 2025-11-03..2026-06-30, validate
  2026-07-01..2026-08-31, test 2026-09-01..2026-09-28 (once per change).
  Live snapshots from 2026-09-29 are a separate forward test.
- A change = new strategy_version compared on held-out data; weights are
  never fitted on the whole dataset.
- Operations: Windows Task Scheduler weekdays — 08:40 worker (repeats every
  5 min until 15:25 so a dead worker relaunches), 15:45 label + reports
  (`scripts/live_day.ps1`, `logs/`). The PC must be on and plugged in.
- Biases stated in every use: survivorship, no historical news/depth, one
  day is description, not evidence.

### #27 — Validation of three explore hypotheses (2026-09-29, PRE-REGISTERED, run once)
- Explore finding (whole market, m15): score, RVOL, volume, momentum and
  movement rank NEGATIVELY with forward excess return (the most extended
  movers slightly mean-revert); nothing clears the 0.1% cost.
- VALIDATE split, 44 days, 168k rows: T1 reweight away from extension —
  FAIL (IC improves +0.004..0.006 but stays negative). T2 lunch penalty —
  REMOVE (lunch not worse; confirmed in direction on the test split, 19
  days, INSUFFICIENT but same sign) → strategy 2026-09-29.m16, penalty 0.
  T3 pullback-only list — FAIL (unstable across halves; −0.07..−0.10%
  after cost, not tradeable). Helpers: `src/research/experiments.py`.

### #28 — Exploration round 2: what predicts continuation among the top-50 movers? (2026-09-29, PRE-REGISTERED)
EXPLORE split only, whole market, DAY panel rows, #24 rules. Q11 time of
day (5 windows), Q12 distance above VWAP (0-0.5 vs > 2%), Q13 RSI (50-60 vs
> 80), Q14 ADX quintiles, Q15 NIFTY direction (> 0.5 vs < −0.5%), Q16 5-min
ROC quintiles; IC where ordered. Findings are hypotheses; any rule built
from them is pre-registered and tested once on validate, then test. Not
testable yet: first vs later breakout, pullback depth from the day high.

## Paper trading and backtesting (simulation only)

### #20 — Recommendation strategy entry policy and journal (2026-09-28, updated 2026-10-08)
- `RecommendationStrategy` turns the live ranked list into simulated LONG
  entries (`paper.yaml entry`): category ≥ CANDIDATE, score ≥ 65, best setup
  TRIGGERED, top 10, max 3 open, 1 trade/symbol/day, none after 15:00,
  10 shares. Stop = fill − 0.25 × daily ATR (0.6% fallback), target 1.5R,
  fixed at entry. Unoptimised starting values.
- Journal: append-only JSONL per day (ENTRY / EXIT / MISSED), entries
  immutable; trade id = hash(run, day, symbol, mode, signal time,
  strategy_version); every record carries strategy_version, config hash,
  git commit. Bump `strategy_version` on any scoring/setup/gate change so
  results from different versions are never mixed.
- First replay (2026-09-25): NO TRADES (max score 64.99) — recorded, the
  policy was not loosened to manufacture trades.

### #29 — Simulated broker for paper trading and backtesting (2026-09-29, user directive)
- `Broker` interface (place_order, cancel_order, positions, on_tick) with
  only simulated implementations: `BacktestBroker` (historical 1-min bars)
  and `PaperBroker` (live Groww ticks). Guards in
  `tests/test_broker_interface.py`.
- The same `Strategy` class runs in backtest and paper mode
  (`TradingSession`). Live strategies: `scalp`, `trend` (#31); `recommendation`
  (#20) and `orb` (#30) remain for backtests.
- Fills: market → next bar open (backtest) / next live tick (paper) +
  slippage; limit → only when price trades through it, no slippage; stop →
  touch triggers, fills at trigger or a worse gap open + slippage; stop
  before target when one bar touches both. Square-off 15:15 at the last
  price; no entries after.
- Costs (`config/costs.yaml`, groww.in/pricing 2026-09-29): brokerage
  min(₹20, 0.1%) with ₹5 floor, STT 0.025% sell, stamp 0.003% buy, NSE txn
  0.00297%, SEBI 0.0001%, IPFT 0.0001%, GST 18%.
- Limits: per-symbol position value, max open positions, capital incl.
  charges at placement and at the fill. Directions and restarts: #30.
- Metrics: every round trip after costs; max drawdown; Sharpe from daily
  returns (N/A with < 2 days). Measurements, not probabilities of profit.
- First backtest (ORB long only, 25 stocks, 83 days to 2026-09-25): 330
  trades, win rate 29%, net −₹29,259 on ₹1 lakh, charges ₹18,023.

### #30 — Short side, restart resume, day report (2026-10-08, user request)
- User: "add short positions too … for any open positions set up target,
  stoploss and record the net profit/loss on exit; keep running and record
  the results until end of day".
- Broker: signed positions; an order reducing the position is an exit
  (never flips). Shorts need `broker.allow_short` (on). Short brackets
  mirror (STOP buy above, LIMIT buy below); square-off buys back. A short
  blocks its full notional as margin (MIS leverage ignored). Trades record
  direction, stop_loss, target.
- ORB (`orb.allow_short`, backtests): first close above the 15-min range
  high → long, below the low → short; stop about the other side of the
  range, target 2R; one attempt per symbol per day.
- Live strategies (`paper.yaml strategies`, now #31) each get their own
  broker + ledger run (`paper:<day>:<strategy>`). A restarted worker RESUMES
  the run: positions, cash, closed trades and open stop/target orders are
  rebuilt from the ledger; open positions stay subscribed.
- `reports/paper_<day>.md` (worker stop + 15:45 task) lists each trade's
  entry, stop, target, exit, reason and net P&L. `live_day.ps1` starts the
  worker with `--paper`.

### #31 — Sentiment-led universe; trade only clear setups; scalps on price action (2026-10-08, user directive)
- User: "pick rising or falling stocks based on market sentiment of nifty
  and banknifty, no hardcoded values of 50 or 15 stocks; trade ONLY if you
  see a clear setup, no minimum number of trades; scalping on price action
  only; for trades lasting > 10-15 min use the existing indicators".
- Sentiment (`src/market/sentiment.py`, `universe.yaml scan.sentiment`): per
  index (NIFTY, BANK NIFTY) three votes from today's 1-min closes — change
  vs previous close (index prev close from a morning prep), change since the
  open, last vs 15 min ago — each +1/−1/0 (0 inside ±0.1%). BULLISH when
  both indices score ≥ +1 and the total ≥ +3; BEARISH mirrored; otherwise
  NEUTRAL. Computed each minute by the worker; in `state.json` as
  `sentiment` (additive, schema unchanged).
- Universe (`scan.qualify`): BULLISH → stocks up ≥ 0.5% vs the previous
  close and beating NIFTY; BEARISH → down ≥ 0.5% and lagging NIFTY;
  NEUTRAL → both. Each also needs volume ≥ 1.5x normal for the time of day
  and the price/liquidity filters. Every qualifying stock joins — no top-N.
  The ceiling is Groww's budget (`api_calls_per_minute` 280 of the 300/min
  cap): each followed stock costs a quote + a candle refresh per minute, so
  capacity = (280 − pool batches − index candles) / 2, recomputed each
  minute. Falling (SHORT) names are left out of research snapshots; on
  bearish days the long-only research panel is therefore thin.
  Historical research replays keep the pre-registered top-50 up-mover
  selection (`replay_top_n`, #26).
- Trading (both strategies): BULLISH → longs only, BEARISH → shorts only,
  NEUTRAL → no new trades. Only stocks moving with the market (long: above
  the previous close; short: below). No minimum trade count. Shorts run the
  long rules on a mirrored chart (2K − price around the previous close), so
  one rule set serves both sides. ₹500 risk per trade, ≤ ₹25k per position.
- `scalp` (1-min, price action only — no indicators): TIGHT_BREAKOUT (strong
  bar closing out of a tight 6-bar base at a new 20-bar high, above the
  day's open; stop below the base) or HIGHER_LOW (higher high, ≥ 2-bar
  pullback holding above the last swing low, close through the prior bar's
  high; stop below the pullback). Target 1.5R, time exit after 15 min,
  ≤ 2 trades per stock per day, entries 09:25-15:00.
- `trend` (trades meant to last > 10-15 min): a FRESH trigger of ORB15,
  VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK or PDH on the 5-min bar that just
  closed, confirmed on 1-min bars by close > VWAP, EMA9 > EMA20, ADX ≥ 20,
  RSI 50-75 and Supertrend up (all required; warming up = no trade). Stop
  1 x the 5-min average true range, target 2R, no time exit, entries
  09:30-14:30.
- All thresholds are starting values, unvalidated. Charges are about ₹56 per
  round trip on a ₹25k position, a large share of a 1.5R scalp; backtest
  before trusting any of it.

### #32 — Same-bar signals take slots strongest first, not alphabetically (2026-10-08, user request)
- Found in the 8 Oct backtest: with 3 open positions and symbols checked
  A→Z, names starting A-C took most slots whenever several stocks signalled
  on the same bar (scalp: 83 of 118 trades).
- Now `DirectionalStrategy.on_bars` collects every signal for the bar, then
  places orders strongest first: strength = % move since the previous close
  in the trade's direction (mirrored for shorts); the symbol breaks ties.
  The index move is the same for every stock on a bar, so ranking "relative
  to NIFTY" gives the same order and is not subtracted. Each signal records
  `strength_pct`.
- Not tuned, not validated. 8 Oct rerun: scalp −₹8,833 → −₹8,360 (A-C share
  83/118 → 29/113); trend 35 → 6 trades (−₹2,200 → −₹959) because the three
  strongest movers at 09:45 had wide ATR stops and, with no time exit, held
  all slots for hours. Whether strongest-first helps is a question for the
  multi-day backtest on the validate/test splits (#24).
