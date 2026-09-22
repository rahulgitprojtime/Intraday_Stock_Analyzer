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
