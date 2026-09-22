# Intraday Scanner

Live Indian-market intraday research and signal-ranking platform on the
Groww Trading API. **Identifies and ranks liquid intraday candidates using
transparent, configurable, backtestable signals — it does not predict or
guarantee profit.**

**Scope: NSE/BSE cash equity, intraday, only.** F&O (derivatives) is
explicitly out of scope — see `DECISIONS.md` #6.

Status: **M0 (foundation) complete.** No live trading, no real Groww calls
yet — see `PROJECT_STATE.md` for exactly what exists and `TODO.md` for
what's next.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env with your Groww credentials (never commit this file)
```

## Running tests

```bash
pytest
```

## Configuration

All tunable behavior lives in `config/*.yaml`, not in code:
- `settings.yaml` — mode (`DATA_ONLY` by default), risk limits, storage.
- `strategy.yaml` — scoring weights (must sum to 100), indicator params.
- `universe.yaml` — symbol universe and liquidity filters.

## Modes

`DATA_ONLY → SIGNAL_ONLY → PAPER_TRADING → LIVE_TRADING`, set via
`APP_MODE` / `config/settings.yaml`. `LIVE_TRADING` additionally requires
`LIVE_TRADING_CONFIRMED=true` — two independent switches, so a config typo
can't enable real order placement. Default is always `DATA_ONLY`.

## Documentation map

- `ARCHITECTURE.md` — module layout and hard boundaries between layers.
- `DECISIONS.md` — why things are built the way they are.
- `docs/groww_api_notes.md` — verified (not assumed) Groww API facts.
- `CLAUDE.md` — working agreement for AI-assisted sessions on this repo.

## Disclaimer

This tool surfaces configurable, explainable signals for research purposes.
No score represents a probability of profit unless explicitly validated
against out-of-sample data, and none of this constitutes financial advice.
