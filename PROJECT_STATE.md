# PROJECT_STATE.md

Last updated: 2026-09-25

## Product
Live intraday **recommendation** dashboard (NSE cash equities). Groww is a
market-data source only; the app never places orders (DECISIONS.md #8).

## Current milestone: M2 — Instrument universe ✅ → next: M3 Live feed

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

### Tests
29 passing locally (`.venv`, Python 3.13, pytest). `growwapi` is not
installed in the venv; adapter tests use `tests/fakes/fake_groww.py`.

### Key facts / known issues
- Feed LTP payload has **no volume**, so volume-based features come from
  1-min historical candles (DECISIONS.md #9). Biggest design constraint.
- Sector is not in the instrument CSV; M7 needs a maintained sector map.
- `GrowwFeed` reconnect behavior is undocumented; M3 must handle it
  defensively (tick-age heartbeat, resubscribe on reconnect).
- M1 is unvalidated against the real API (needs credentials in `.env`).

### Next task
M3: `GrowwFeed` wrapper in `src/broker/` (LTP + index + depth by
exchange_token, tick normalization, dedupe, stale detection) with fake-feed
tests; then the standalone live data worker.
