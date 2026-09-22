# Groww Trading API — Verified Notes (M0)

Source of truth: https://groww.in/trade-api/docs (Python SDK section),
fetched 2026-09-23. **Re-verify against the live docs before implementing
M1** — API surfaces change and this file is a snapshot, not a mirror.

## SDK

`pip install growwapi` (Python 3.9+). Requires an active Trading API
subscription on the Groww account.

## Auth (two flows)

1. **API Key + Secret** — generate at the Groww Cloud API Keys page.
   Access token expires daily; needs the key/secret to mint a fresh token
   each day.
   ```python
   from growwapi import GrowwAPI
   access_token = GrowwAPI.get_access_token(api_key=..., secret=...)
   groww = GrowwAPI(access_token)
   ```
2. **TOTP** — generate a TOTP token/secret at the same page. No expiry;
   requires `pyotp` to generate the rolling code each time a token is
   requested.
   ```python
   import pyotp
   totp = pyotp.TOTP(secret).now()
   access_token = GrowwAPI.get_access_token(api_key=totp_token, totp=totp)
   ```

Both env-drivable — `.env.example` has placeholders for both; pick one via
`GROWW_AUTH_MODE`.

## Rate limits (per type, not per endpoint — one exhausted call throttles
its whole type)

| Type           | Per second | Per minute | Notes |
|----------------|-----------|------------|-------|
| Authentication | 5         | 30         | `/v1/token/api/access` also capped at 150/24h |
| Orders         | 10        | 250        | create/modify/cancel |
| Live Data      | 10        | 300        | quote/LTP/OHLC |
| Non-Trading    | 20        | 500        | order status/list, trades, positions, holdings, margin |

Live feed (`GrowwFeed`): up to 1000 subscriptions at a time.

## Live data methods

- `groww.get_quote(exchange, segment, trading_symbol)` — full quote incl.
  OHLC, depth (top level), bid/offer, 52w high/low, day change, etc.
- `groww.get_ltp(segment, exchange_trading_symbols)` — up to 50 instruments
  per call, e.g. `exchange_trading_symbols=("NSE_NIFTY", "NSE_RELIANCE")`.
- `groww.get_ohlc(segment, exchange_trading_symbols)` — real-time snapshot
  OHLC (current-time OHLC, not historical candles), up to 50 instruments.
- `groww.get_option_chain(exchange, underlying, expiry_date)` — full chain
  with Greeks per strike. **Out of scope for this project (F&O excluded,
  see DECISIONS.md #6)** — documented here only for completeness of the
  verified API surface, not wrapped in `BrokerAdapter`.
- `groww.get_greeks(exchange, underlying, trading_symbol, expiry)`. **Out of
  scope, same reason.**

## Historical candles

`groww.get_historical_candle_data(...)` is **deprecated** — current method
is `get_historical_candles` (verify exact signature at implementation
time; the deprecated method's shape below is likely close but not
guaranteed identical).

Request: `trading_symbol`, `exchange`, `segment`, `start_time`, `end_time`
(either `YYYY-MM-DD HH:mm:ss` or epoch millis), `interval_in_minutes`.

Response: `candles` = list of `[epoch_seconds, open, high, low, close,
volume]`.

Per-interval request-window and history-depth limits:

| Interval | Max window/request | History available |
|----------|--------------------|--------------------|
| 1 min    | 7 days             | 3 months |
| 5 min    | 15 days            | 3 months |
| 10 min   | 30 days            | 3 months |
| 1 hour   | 150 days           | 3 months |
| 4 hours  | 365 days           | 3 months |
| 1 day    | ~3 years (1080d)   | Full history |
| 1 week   | No limit           | Full history |

This directly constrains M5 (historical storage): fetching a full 3-month
1-min history requires ~13 chunked requests (7-day windows), not one call.

## Live feed (`GrowwFeed`)

```python
from growwapi import GrowwFeed, GrowwAPI
groww = GrowwAPI(access_token)
feed = GrowwFeed(groww)
feed.subscribe_ltp(instruments_list, on_data_received=callback)
feed.consume()  # blocking — run in its own thread/process, never in Streamlit's
```

- Instrument identity for feed subscriptions is `exchange_token` (from the
  instruments CSV: https://growwapi-assets.groww.in/instruments/instrument.csv),
  not trading symbol.
- Supports: LTP (equity/derivatives), index value, market depth (buy/sell
  book), equity order updates, F&O order updates, F&O position updates.
- Both sync (poll `feed.get_ltp()` etc.) and async (`on_data_received`
  callback + `feed.consume()`) usage patterns.
- No documented native reconnect/heartbeat details captured yet — M3 must
  verify current behavior (does the SDK auto-reconnect? what does a dropped
  connection look like to the caller?) before finalizing stale-data
  detection logic.

## Segments / exchanges (annexures — not yet fetched in detail)

Groww exposes `groww.EXCHANGE_NSE`, `groww.SEGMENT_CASH`, `groww.SEGMENT_FNO`,
etc. as SDK constants rather than raw strings — use the SDK constants, not
hand-typed strings, once `groww.py` is implemented. Full annexure table
(all exchange/segment/product/order-type constants) should be fetched at
M1 start: https://groww.in/trade-api/docs/python-sdk/annexures

## Open items to verify at M1 (do not assume)

- Exact signature and response shape of `get_historical_candles`
  (replacement for the deprecated method).
- Order placement fields beyond the sample in the intro page (validity,
  product, order_type, transaction_type constants — full list in annexures).
- Exception/error types (`docs/python-sdk/exceptions`) for retry/backoff
  logic.
- Whether market depth beyond top-of-book is available via `get_quote`
  vs. only via the streaming feed's `get_market_depth()`.
