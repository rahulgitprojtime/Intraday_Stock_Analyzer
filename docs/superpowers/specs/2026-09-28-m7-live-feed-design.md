# M7 — Live feed + depth: design

Date: 2026-09-28 · Status: user-approved in chat (3 sections), pending spec review
Scope: LONG-only recommendations (DECISIONS #8, #11). Read-only market data; no orders.

## 1. Goal and success criteria

Stream LTP, NIFTY index value and market depth from `GrowwFeed` during
market hours, and use it for (a) feed freshness, (b) a spread liquidity
gate and (c) a small SCALP-only microstructure score component (bid/ask
imbalance, tick velocity).

Success: during market hours `python -m src.app.worker` runs live; the
dashboard shows fresh SCALP/DAY rankings, a feed status, and spread /
imbalance / velocity reasons for ranked names. Replay output is unchanged.

User decisions (2026-09-28): approach "in-process feed thread + FeedStore";
feed impact "spread gate + small SCALP weight".

## 2. Verified constraints

- Feed payloads carry no volume (DECISIONS #9): candles, RVOL, VWAP stay on
  1-min REST history. Payload shapes: `docs/groww_api_notes.md` "Live feed".
- SDK source (growwapi 1.5.0, read 2026-09-28): `GrowwFeed(groww_api)`
  mints a socket token and starts a NATS client on a daemon thread;
  `consume()` only joins it. nats-py default reconnect + auto-resubscribe;
  disconnect/closed callbacks only log — the caller is never notified. The
  SDK stores only the latest value per topic. Consequences: staleness by
  tick age, own restart logic, own tick counting in the callback.
- Subscriptions: max 1000; universe is 25 stocks + NIFTY → subscribe all,
  no dynamic top-N.

## 3. Components

| Unit | Responsibility | Depends on |
|------|----------------|-----------|
| `src/broker/groww_feed.py` `LiveFeed` | Only feed code touching Groww. Build `GrowwFeed` from the authenticated adapter's client; subscribe by `exchange_token` (LTP + depth per stock, index value for NIFTY); parse payloads into `Tick`/`DepthSnapshot`; push to a sink. `start()`, `stop()`, `restart()`. | growwapi, `src/data/models` |
| `src/data/models.py` | `Tick(symbol, ts, ltp)`, `DepthSnapshot(symbol, ts, best_bid, bid_qty, best_ask, ask_qty, total_bid_qty, total_ask_qty)` | — |
| `src/market/feed_store.py` `FeedStore` | Thread-safe (one lock). Drops a tick whose (ts, ltp) equals the last one for the symbol and any tick older than the last. Keeps latest LTP, latest depth, deque of tick timestamps (last 5 min). `snapshot(now) -> FeedSnapshot` (immutable). Counts rejected/bad payloads. | models only |
| `src/quantitative/microstructure.py` | Pure fns: `spread_pct`, `imbalance`, `tick_velocity`, `micro_score`, `symbol_feed(snapshot, symbol, now, stale_s) -> SymbolFeed \| None` | config dict |
| `src/app/feed_watchdog.py` `FeedWatchdog` | Feed status + restart policy (§6), driven by an injected clock. | `LiveFeed`-like object, `FeedStore` |
| Engine / liquidity / schema / dashboard | Consume `SymbolFeed` (§4–5). | — |

`FeedSnapshot` per symbol: `last_tick_age_s`, `ticks_1m`, `ticks_5m_avg`
(ticks in last 5 min / 5), `depth` (latest `DepthSnapshot` or None),
`depth_age_s`; feed-wide `last_any_tick_age_s`, `bad_payloads`.

`SymbolFeed`: `spread_pct`, `imbalance`, `tick_velocity`, `micro_score`,
`last_tick_age_s`, `depth_age_s`. `symbol_feed` returns None when the
symbol's last tick is older than `stale_after_seconds`; spread/imbalance
are None when depth is missing or older than `stale_after_seconds`.

## 4. Metrics

- `spread_pct = (best_ask − best_bid) / ((best_ask + best_bid)/2) × 100`;
  None if either side ≤ 0 or ask < bid.
- `imbalance = (total_bid_qty − total_ask_qty) / (total_bid_qty + total_ask_qty)`
  over all levels in the depth payload, in [−1, +1]; None if total is 0.
- `tick_velocity = ticks_1m / max(ticks_5m_avg, 1)`.
- `micro_score` (0–100) = 60% × ramp(imbalance, 0.0 → 0.4) + 40% ×
  ramp(tick_velocity, 1.0 → 2.5); a missing input drops out and the other
  is renormalized; both missing → None. Weights and ramps in
  `strategy.yaml` `microstructure:`. Illustrative defaults, unvalidated
  (M10).

## 5. Data flow and scoring

Live worker: authenticate → resolve universe → `prepare()` (REST) →
`LiveFeed.start()` → each minute: REST 1-min refresh (unchanged) →
`store.snapshot(as_of)` → `SymbolInputs.feed = symbol_feed(...)` →
`evaluate_symbol` → state. Replay: `feed=None`, feed status `OFF`.

- `SymbolInputs.feed: SymbolFeed | None = None` (default keeps callers and
  replay unchanged).
- **Spread gate (both modes):** `evaluate_liquidity(..., spread_pct=None)`;
  when not None and > `filters.max_spread_pct` (universe.yaml, 0.5) →
  fail "spread above maximum" → excluded as `liquidity: ...`. None →
  reason keeps "spread unchecked".
- **`microstructure` component:** `engine.weights` becomes setup 0.50,
  in_play 0.30, market_context 0.10, microstructure 0.10. Component is
  AVAILABLE only in SCALP with a non-None `micro_score`; in DAY and when
  missing it is UNAVAILABLE and excluded from the blend (existing
  renormalization). Note: DAY and replay SCALP blends renormalize over
  setup/in_play/market, i.e. 0.50/0.30/0.10 → 0.556/0.333/0.111 instead
  of 0.55/0.35/0.10 — a small shift, flagged for the user at spec review.
  Tests pinning exact scores are updated deliberately in the same commit.
  Alternative if rejected: per-mode weights so DAY keeps 0.55/0.35/0.10.
- **Reasons** (evidence in `quantitative.microstructure`): "Spread 0.04%",
  "Bid/ask imbalance +0.32 (more bids)", "Tick velocity 1.8x the 5-min
  average". Bid/ask prices are never emitted (no price levels, #11).
- **State schema v4:** additive top-level `feed: {status, last_tick_age_s,
  restarts, subscribed, bad_payloads}`; `status ∈ LIVE|STALE|DOWN|OFF`.
  Validator + dashboard header (feed status next to data age) updated.

## 6. Failure handling

- **Start failure** (token error, `GrowwFeedConnectionException`, any
  exception): worker continues REST-only; status `DOWN`; error recorded;
  retry per the restart policy.
- **Watchdog** (only 09:15–15:30): `last_any_tick_age_s` > 30 s → `STALE`;
  > 60 s → `DOWN` and `restart()` (stop old feed, new `GrowwFeed` = new
  socket token, resubscribe). At most one restart per minute; after 5
  consecutive failed restarts, retry every 5 min. A tick arriving resets
  to `LIVE` and the failure count. Thresholds in `settings.yaml` `feed:`.
- **Per-symbol staleness:** handled by `symbol_feed` → None; never
  triggers a restart.
- **Bad payloads:** missing/zero/non-numeric fields are skipped and
  counted; the callback never raises and holds the lock only to append.
- **Shutdown:** at session end or KeyboardInterrupt, `stop()` unsubscribes;
  the SDK thread is a daemon.

## 7. Testing

TDD, no network:
- `FeedStore`: dedupe, out-of-order, 5-min window, snapshot immutability,
  concurrent writers smoke test.
- Microstructure: reference values, None cases, ramps, renormalization.
- `LiveFeed` parsing with `tests/fakes/fake_groww_feed.py` using the
  documented payload shapes; bad payloads counted, not raised.
- `FeedWatchdog` transitions with a fake clock (LIVE→STALE→DOWN→restart,
  backoff after 5 failures, outside-hours no-op).
- Engine: spread gate excludes; component SCALP-only; DAY unaffected by
  feed; `feed=None` path; reasons traceable; no price fields.
- Schema v4 validator; dashboard AppTest shows feed status.

Live (read-only): `scripts/feed_smoke.py` — 3 stocks + NIFTY for 2 min,
prints tick counts, depth shape, computed spread/imbalance; then one
~15-min live worker run with the dashboard. Findings →
`docs/groww_api_notes.md`.

## 8. Out of scope

Tick-built candles; dynamic top-N subscription; NIFTY context from the
index feed (stays on REST bars); depth beyond what the payload carries;
any order/position feeds.
