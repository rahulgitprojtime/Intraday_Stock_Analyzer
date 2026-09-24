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

## Historical candles (current method — verified from the Backtesting page)

`groww.get_historical_candle_data(...)` (documented in the Historical Data
page) is **deprecated**. The current method is `get_historical_candles`,
documented on the **Backtesting** page — its request shape is meaningfully
different from the deprecated one, not just a rename:

```python
resp = groww.get_historical_candles(
    exchange=groww.EXCHANGE_NSE,
    segment=groww.SEGMENT_CASH,
    groww_symbol="NSE-WIPRO",              # NOT trading_symbol
    start_time="2025-09-24 10:56:00",
    end_time="2025-09-24 12:00:00",
    candle_interval=groww.CANDLE_INTERVAL_MIN_30,   # enum-like constant, NOT interval_in_minutes
)
```

- **`groww_symbol`**, not `trading_symbol` — format is `EXCHANGE-SYMBOL`
  (hyphen), e.g. `NSE-WIPRO`, `BSE-RELIANCE`. For equities/indices it's just
  exchange + trading symbol (no expiry/strike/option-type components, those
  only apply to F&O and are out of scope here anyway).
- **`candle_interval`** takes an SDK constant (see below), not a raw int.
- Response candle rows are `[timestamp_str, open, high, low, close, volume,
  open_interest]` — note **7 fields**, not 6 (the extra is `open_interest`,
  `null` for non-FNO instruments — always null/ignored for us). Timestamp is
  a string like `"2025-09-24T10:30:00"` (ISO-ish, `T` separator observed in
  the docs' own example despite the schema table saying
  `yyyy-MM-dd HH:mm:ss` — parse defensively for both).
- Response also includes `closing_price`, `start_time`, `end_time`,
  `interval_in_minutes` at the top level.
- Data available from **2020** for equities, indices, and FNO.

### Backtesting/historical data limits (current — supersedes the deprecated
method's table above, which should no longer be relied on)

| Candle interval(s)                          | Max window per request |
|----------------------------------------------|-------------------------|
| 1, 2, 3, 5 min                                | 30 days |
| 10, 15, 30 min                                | 90 days |
| 1 hour, 4 hours, 1 day, 1 week, 1 month       | 180 days |

This is what `src/broker/groww.py`'s `get_historical_candles` chunks
against — it splits a caller's requested [start, end] range into windows no
larger than the table above and stitches the results back together.

### Candle interval constants (verified from Annexures page)

| Constant | Value |
|---|---|
| `CANDLE_INTERVAL_MIN_1` | `1minute` |
| `CANDLE_INTERVAL_MIN_2` | `2minute` |
| `CANDLE_INTERVAL_MIN_3` | `3minute` |
| `CANDLE_INTERVAL_MIN_5` | `5minute` |
| `CANDLE_INTERVAL_MIN_10` | `10minute` |
| `CANDLE_INTERVAL_MIN_15` | `15minute` |
| `CANDLE_INTERVAL_MIN_30` | `30minute` |
| `CANDLE_INTERVAL_HOUR_1` | `1hour` |
| `CANDLE_INTERVAL_HOUR_4` | `4hour` |

(Day/week/month constants exist per the limits table above but weren't
captured verbatim in this pass — verify before using them; not needed yet
since `config/settings.yaml` only requests 1/3/5/15-min candles.)

### Exchange / Segment constants (verified from Annexures page)

| Constant | Value |
|---|---|
| `EXCHANGE_NSE` | `NSE` |
| `EXCHANGE_BSE` | `BSE` |
| `EXCHANGE_MCX` | `MCX` (commodities — not used; not a Groww Trading API segment we touch) |
| `SEGMENT_CASH` | `CASH` — **the only segment this project uses** |
| `SEGMENT_FNO` | `FNO` — out of scope (DECISIONS.md #6) |
| `SEGMENT_COMMODITY` | `COMMODITY` — out of scope |

## Verified SDK exceptions (`growwapi.groww.exceptions`)

```
GrowwBaseException                     # root of everything
└── GrowwAPIException                  # has .msg, .code
    ├── GrowwAPIAuthenticationException
    ├── GrowwAPIAuthorisationException
    ├── GrowwAPIBadRequestException
    ├── GrowwAPINotFoundException
    ├── GrowwAPIRateLimitException
    └── GrowwAPITimeoutException
GrowwFeedException                     # separate branch, for streaming (M3)
├── GrowwFeedConnectionException
└── GrowwFeedNotSubscribedException
```

`src/broker/groww.py` translates these into this project's own
`BrokerAdapterError` / `AuthenticationError` / `RateLimitError` at the
adapter boundary, per DECISIONS.md #4 (nothing above the adapter should
import or catch `growwapi` exception types directly). `RateLimitError` and
`GrowwAPITimeoutException` are retried with backoff via
`src/utils/retry.py` before being allowed to propagate.

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

- Whether market depth beyond top-of-book is available via `get_quote`
  vs. only via the streaming feed's `get_market_depth()`.
- Exact `get_access_token` error behavior for expired/invalid TOTP secrets
  (which exception fires) — implement against `GrowwAPIAuthenticationException`
  per the verified exceptions list below, adjust if real testing shows
  otherwise.
- Order placement fields (validity, product, order_type, transaction_type
  constants) are NOT needed for this project — no order execution is in
  scope until/unless PAPER_TRADING or LIVE_TRADING modes are explicitly
  built (M12+), and even then this is cash-equity-only (DECISIONS.md #6).
