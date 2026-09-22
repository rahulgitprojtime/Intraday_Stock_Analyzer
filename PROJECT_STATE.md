# PROJECT_STATE.md

Last updated: 2026-09-23 (M0 bootstrap)

## Current milestone: M0 — Environment + Skills + Architecture

**Status: foundation laid, not yet validated against a live Groww account.**

### What exists
- Repo skeleton (`config/`, `src/*`, `tests/`, `data/*`, `docs/`) — see
  `ARCHITECTURE.md` for the layout and rationale.
- Tracking docs: this file, `TODO.md`, `DECISIONS.md`, `ARCHITECTURE.md`,
  `CLAUDE.md`, `README.md`.
- `pyproject.toml` with pinned-family dependencies (no versions frozen yet —
  see DECISIONS.md #2).
- `.env.example`, `.gitignore` — no secrets in repo.
- `src/broker/base.py` — `BrokerAdapter` abstract interface derived from
  verified Groww Python SDK capabilities (see `docs/groww_api_notes.md`).
  **Not implemented yet** — `src/broker/groww.py` is a stub that raises
  `NotImplementedError`. Real implementation is M1.
- `src/data/models.py` — typed dataclasses for Quote, OHLC, Candle,
  MarketDepth, HistoricalCandleRequest — shared vocabulary across layers.
- `config/settings.yaml`, `config/strategy.yaml`, `config/universe.yaml` —
  placeholder configs matching the shape described in the spec (scoring
  weights, risk limits, universe filters). Values are illustrative defaults,
  not tuned.
- `tests/test_config.py`, `tests/test_broker_interface.py` — first tests;
  verify config loads and the abstract interface can't be instantiated
  without implementing every method. No live API calls in any test.

### What does NOT exist yet
Everything from M1 onward: real Groww auth, live feed, candle engine,
indicators, signal engine, scoring, risk engine, backtesting, paper trading,
Streamlit dashboard. See `TODO.md`.

### Known constraints from verified Groww docs (full notes in
`docs/groww_api_notes.md`)
- Two auth flows: API-Key+Secret (daily-expiring access token) or TOTP
  (no expiry). Both go through `GrowwAPI.get_access_token(...)`.
- Rate limits are per **type**, not per endpoint: Auth 5/s·30/min,
  Orders 10/s·250/min, Live Data 10/s·300/min, Non-Trading 20/s·500/min.
  `/v1/token/api/access` additionally capped at 150/24h.
- `get_ltp` / `get_ohlc` take up to 50 instruments per call.
- Live streaming (`GrowwFeed`) supports up to 1000 subscriptions at a time;
  synchronous polling or async callback via `feed.consume()`.
- Historical candle data: max request window depends on interval (e.g. 1-min
  candles: 7-day window per request, 3 months of history available; daily
  candles: ~3-year window per request, full history). Full table in
  `docs/groww_api_notes.md`.
- `get_historical_candle_data` is **deprecated** in favor of
  `get_historical_candles` — implement against the current method in M1,
  verify exact signature against docs at implementation time (docs may have
  moved since this note was written).

### Scope (confirmed by user, 2026-09-23)
Cash equity only, intraday. **F&O is out of scope** — see DECISIONS.md #6.
`Segment` has only `CASH`; no option-chain/Greeks methods on the adapter.

### Open questions for the user (none blocking M0)
- Which Groww auth flow will be used (API key/secret vs TOTP)? Affects
  `.env.example` fields. Currently both are stubbed.

### Next action
Begin M1 (Groww authentication + API adapter) only when the user confirms
they want to proceed, since M1 requires real credentials to test against.
