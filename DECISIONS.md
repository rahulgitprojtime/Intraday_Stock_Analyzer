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
