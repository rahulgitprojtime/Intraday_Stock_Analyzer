# TODO.md

Checklist by milestone. Check items off as completed; keep this file short —
move detail into `PROJECT_STATE.md` or `DECISIONS.md` rather than growing
this list with sub-notes.

## M0 — Environment + Skills + Architecture ✅ (this session)
- [x] Inspect environment/skills/tools
- [x] Verify current official Groww API docs (see `docs/groww_api_notes.md`)
- [x] CLAUDE.md / PROJECT_STATE.md / TODO.md / DECISIONS.md / ARCHITECTURE.md
- [x] Repo skeleton
- [x] pyproject.toml, .env.example, .gitignore
- [x] Broker adapter interface (`src/broker/base.py`) + stub
- [x] Shared data models (`src/data/models.py`)
- [x] Placeholder config files
- [x] First tests (config load, interface contract)

## M1 — Groww authentication + API adapter
- [ ] Implement `src/broker/groww.py` against `growwapi` SDK
- [ ] Support both API-key+secret and TOTP auth flows (env-driven)
- [ ] Token refresh / expiry handling
- [ ] Wrap: get_quote, get_ltp, get_ohlc, get_historical_candles
- [ ] Rate-limit handling per Groww's per-type limits
- [ ] API error handling + retries with backoff
- [ ] Tests with mocked SDK responses (no live calls in CI)

## M2 — Instrument universe
- [ ] Load/cache instrument master (exchange token lookup)
- [ ] Universe config resolution (symbols → exchange tokens)

## M3 — Live market feed
- [ ] `GrowwFeed` wrapper: subscribe/unsubscribe LTP, depth
- [ ] Reconnection logic, stale-data detection
- [ ] Feed runs independently of Streamlit (background process/thread)

## M4 — Candle engine
- [ ] Build 1/3/5/15-min candles from feed ticks
- [ ] Handle missing/duplicate/delayed ticks, market open/close edges

## M5 — Historical storage
- [ ] Local cache (Parquet/SQLite) for historical candles
- [ ] Incremental fetch (don't re-download what's cached)

## M6 — Indicators/features
- [ ] Trend: EMA9/20/50, SMA, VWAP, ADX, Supertrend
- [ ] Momentum: RSI, MACD, ROC
- [ ] Volatility: ATR, Bollinger Bands + width
- [ ] Volume: RVOL (time-of-day adjusted), OBV, volume acceleration
- [ ] Market structure: prior day H/L, opening range, VWAP distance
- [ ] Unit tests against known reference values for every indicator

## M7 — Liquidity scanner
- [ ] Configurable filters (price, ADV, spread) — cash equity only
- [ ] Liquidity score (not just raw volume)

## M8 — Signal engine
- [ ] Multi-factor rule engine → LONG_SETUP / SHORT_SETUP / NEUTRAL / AVOID
- [ ] Machine-readable positive/negative factor lists per signal

## M9 — Scoring/ranking
- [ ] Configurable weighted scoring (`config/strategy.yaml`)
- [ ] Ranking engine, train/validation/out-of-sample separation

## M10 — Risk engine
- [ ] Entry/stop/target, ATR-based sizing, R:R, exposure limits
- [ ] Fully independent from signal engine

## M11 — Backtesting
- [ ] Event-driven, no-look-ahead engine
- [ ] Realistic costs (brokerage, STT, GST, stamp duty, SEBI charges, slippage)
- [ ] Metrics: win rate, expectancy, profit factor, drawdown, Sharpe/Sortino,
      CAGR, exposure, turnover
- [ ] Walk-forward / out-of-sample support

## M12 — Paper trading
- [ ] DATA_ONLY / SIGNAL_ONLY / PAPER_TRADING / LIVE_TRADING modes
- [ ] LIVE_TRADING defaults false; execution isolated from signal generation

## M13 — Streamlit dashboard
- [ ] Market overview page
- [ ] Scanner table with filters/sorting
- [ ] Stock detail view + "Why this stock?" explainability panel

## M14 — Performance optimization
- [ ] Caching, incremental recompute, avoid redundant API/db calls

## M15 — Testing/security/documentation
- [ ] Full test coverage per Phase 17 list
- [ ] Secret-scanning check
- [ ] Docs: setup, config, adding indicators/strategies/brokers
