# CLAUDE.md — Working Agreement for AI-Assisted Development

This file tells any AI assistant (or human) picking up this repo how to work
in it efficiently. Read this file first, every session.

## Read order (cheapest → most expensive)

1. `PROJECT_STATE.md` — what's built, what's in progress, current milestone.
2. `TODO.md` — the active task list.
3. `DECISIONS.md` — architectural decisions already made (don't re-litigate).
4. Only then open source files relevant to the *current* task.

Do not re-read `ARCHITECTURE.md` or this file's history unless something
structural changed. Do not paste whole files into chat/responses unless the
user explicitly asks — prefer targeted diffs.

## Ground rules

- **Never fabricate Groww API behavior.** The adapter (`src/broker/`) is the
  only place that talks to Groww. If unsure of an endpoint, method name, or
  payload shape, check `docs/groww_api_notes.md` (verified facts only) or the
  official docs at https://groww.in/trade-api/docs — do not guess.
- **Never use future data in backtests.** The backtest engine only exposes
  information available at or before timestamp T.
- **Never hard-code secrets.** Credentials come from environment variables
  (`.env`, not committed). `.env.example` holds placeholders only.
- **Keep layers separated**: broker adapter → data layer → candle engine →
  feature engine → signal engine → scoring → risk → ranking → UI. A layer
  must not reach past its neighbor (e.g. Streamlit must never import from
  `src/broker` directly — it consumes processed state).
- **No score is a probability of profit** unless it has been statistically
  validated against walk-forward/out-of-sample data. Say so in the UI.
- **Default to safety**: `LIVE_TRADING=false` unless a human explicitly flips
  it. Modes are `DATA_ONLY`, `SIGNAL_ONLY`, `PAPER_TRADING`, `LIVE_TRADING`.
- **Don't over-engineer.** No abstraction for a hypothetical second broker
  until a second broker is actually being added. The `BrokerAdapter`
  interface exists because Groww integration must stay swappable/testable,
  not because we expect to add brokers soon.
- **Update tracking files as you go**: `PROJECT_STATE.md` after each
  milestone, `TODO.md` as tasks complete, `DECISIONS.md` for anything that
  would surprise a future session.

## Milestone map

See `PROJECT_STATE.md` for current status. Full sequence in `TODO.md`.

## Running things

See `README.md` for setup/run instructions (kept current as the project
grows — do not duplicate that content here).
