# PROJECT_STATE.md

Last updated: 2026-09-23 (M1 complete)

## Current milestone: M1 — Groww authentication + API adapter ✅

**Status: implemented and tested against mocked SDK responses. Not yet run
against a real Groww account — that's the natural next validation step
whenever real credentials are available.**

### What exists (cumulative)
- Everything from M0 (see git history / DECISIONS.md), now with scope
  confirmed as **cash equity only — F&O excluded** (DECISIONS.md #6).
- `src/broker/groww.py` — real implementation:
  - `authenticate()` supports both flows (`GROWW_AUTH_MODE=api_key` or
    `totp`), calling `GrowwAPI.get_access_token(...)` per the verified
    docs, then constructing `GrowwAPI(access_token)`.
  - `get_quote`, `get_ltp` (chunked at 50 instruments/call), `get_ohlc`
    (chunked at 50), all mapping SDK responses onto this project's own
    `Quote`/`OHLC`/`MarketDepth` dataclasses.
  - `get_historical_candles` against the **current** (non-deprecated)
    method — `groww_symbol` format, `candle_interval` SDK constants,
    request-window chunking per Groww's actual per-interval limits (30
    days for 1-5min, 90 for 10-30min, 180 for 1hr+), with de-duped
    stitching across windows.
  - All `growwapi.groww.exceptions.*` types translated into this project's
    own `AuthenticationError` / `RateLimitError` / `BrokerAdapterError` at
    the adapter boundary — nothing above `src/broker/` ever imports or
    catches a `growwapi` exception directly.
  - Rate-limit and timeout errors retried with exponential backoff via a
    small hand-rolled `src/utils/retry.py` (DECISIONS.md #7 — chose not to
    add `tenacity` as a dependency for something this small).
  - `growwapi`/`pyotp` are imported defensively (fall back to placeholders
    if not installed) so the module — and its tests — don't require the
    real packages to be present.
  - Streaming methods (`subscribe_ltp`, `get_market_depth`,
    `connection_state`) still raise `NotImplementedError` — that's M3.
- `tests/fakes/fake_groww.py` — a `FakeGrowwAPI` test double shaped to
  match the verified real SDK (same constants, method names, response
  payloads) so tests exercise real logic (auth mode switching, chunking,
  window-splitting, retry/backoff) without needing the real package or
  network.
- `tests/test_groww_adapter.py` — 12 test cases: both auth flows, missing
  env vars, SDK exception translation, calling before auth, quote/LTP/OHLC
  response mapping, LTP chunking over 50 instruments, historical-candle
  window splitting + dedup, unsupported interval rejection, rate-limit
  retry (success-after-retry and exhausted-retries), and FNO-segment
  rejection.
- **Verification caveat**: this sandbox has no network, so `pytest` itself
  couldn't be installed to literally run `tests/test_groww_adapter.py`
  here. Every scenario in that file was instead run through a hand-built
  harness reproducing the same logic (all 12 passed) — the delivered test
  file is the real artifact to run with `pytest` once you have it
  installed locally; treat it as unverified-by-CI until that first local
  run confirms it.

### Corrected API notes (superseding earlier M0 notes)
`docs/groww_api_notes.md` was updated after finding that the **current**
`get_historical_candles` method (documented on the Backtesting page) has a
meaningfully different shape than the deprecated `get_historical_candle_data`
method M0 had only glimpsed: `groww_symbol` (hyphenated, e.g. `NSE-WIPRO`)
instead of `trading_symbol`, `candle_interval` SDK constants instead of a
raw `interval_in_minutes` int, a 7-field candle row (adds open interest),
and a different, more restrictive max-window-per-request table (30/90/180
days depending on interval, not the deprecated method's 7-365 day table).

### Open questions for the user
None blocking. Real credentials + a live smoke test against the actual
Groww API would be the natural way to validate M1 before M2.

### Next action
M2 — Instrument universe (resolve symbols to exchange tokens against the
instrument master CSV, needed before M3's live feed can subscribe to
anything).
