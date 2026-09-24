# PROJECT_STATE.md

Last updated: 2026-09-25

## Product
Live intraday **recommendation** dashboard (NSE cash equities). Groww is a
market-data source only; the app never places orders (DECISIONS.md #8).

## Scope (2026-09-25, DECISIONS.md #11)
LONG-only upward-momentum candidates. Ranking driven by named setups used
by intraday traders (ORB, VWAP, PDH, CPR, EMA pullback, momentum burst),
Scalp (1-min) and Day (5/15-min) modes, stocks-in-play pre-filter,
time-of-day rules. No price levels on cards. Dashboard MVP before live feed.

## Current milestone: M3 — M3a ✅ (MIS filter + daily prep) → next: M3b candle pipeline

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

### Tests
38 passing locally (`.venv`, Python 3.13, pytest). `growwapi` is not
installed in the venv; adapter tests use `tests/fakes/fake_groww.py`.

### Key facts / known issues
- Feed LTP payload has **no volume**, so volume-based features come from
  1-min historical candles (DECISIONS.md #9). Biggest design constraint.
- Sector is not in the instrument CSV; M7 needs a maintained sector map.
- `GrowwFeed` reconnect behavior is undocumented; M3 must handle it
  defensively (tick-age heartbeat, resubscribe on reconnect).
- M1 is unvalidated against the real API (needs credentials in `.env`).

### Next task
M3b: orchestrator that fetches daily + ~20 days of 1-min history per stock
via `BrokerAdapter` and builds DailyPrep + volume curve; then per-minute
incremental 1-min refresh with parquet cache and 3/5/15-min resampling.
