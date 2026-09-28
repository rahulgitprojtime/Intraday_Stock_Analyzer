# Intraday Scanner

Live intraday **stock recommendation dashboard** for Indian cash equities,
using the Groww Trading API as a market-data source. Ranks liquid intraday
candidates by a blended, explainable Recommendation Score (quantitative +
qualitative + market/sector context + liquidity). **It never places orders
and the score is not a probability of profit.**

**Scope: NSE/BSE cash equity, intraday, only.** F&O (derivatives) is
explicitly out of scope — see `DECISIONS.md` #6.

Status: see `PROJECT_STATE.md` (current milestone) and `TODO.md`.

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

## Running the MVP (M6)

The worker writes `data/processed/state.json` every minute; the dashboard
only reads that file. Nothing places orders.

```bash
# Replay a synthetic DEMO day (no credentials needed)
python scripts/make_demo_data.py --out data/demo --day 2026-09-25
python -m src.app.worker --replay data/demo --day 2026-09-25 --speed 60
streamlit run app/dashboard.py        # needs pandas/pyarrow to load

# Replay real sessions (Groww credentials; read-only history download)
python scripts/fetch_replay_data.py --day 2026-09-25 --out data/replay
python -m src.app.worker --replay data/replay --day 2026-09-25 --speed 60

# Live, market hours (Groww credentials + live-price subscription):
# REST 1-min candles + live feed (LTP, depth, NIFTY) in one process (M7)
python scripts/groww_smoke.py         # REST check
python scripts/feed_smoke.py          # live feed check (~2 min)
python -m src.app.worker
```

`INTRADAY_STATE=<path> streamlit run app/dashboard.py --server.port 8502`
points a second dashboard at another state file (e.g. a replay next to
live).

Live mode streams LTP/depth for the whole universe. The dashboard header
shows the feed status (LIVE / STALE / DOWN / OFF); on STALE/DOWN the
worker keeps ranking from REST candles and pauses the spread check and
Scalp microstructure.

`--speed` is simulated minutes per real minute (`0` = as fast as
possible). DEMO data is synthetic and is not strategy evidence.

## Configuration

All tunable behavior lives in `config/*.yaml`, not in code:
- `settings.yaml` — storage, feed, candle timeframes.
- `strategy.yaml` — M6 engine baseline weights, time heuristics, categories,
  in-play scanner, indicator params.
- `universe.yaml` — symbol universe and liquidity filters.

## Documentation map

- `ARCHITECTURE.md` — module layout and hard boundaries between layers.
- `DECISIONS.md` — why things are built the way they are.
- `docs/groww_api_notes.md` — verified (not assumed) Groww API facts.
- `CLAUDE.md` — working agreement for AI-assisted sessions on this repo.

## Disclaimer

This tool surfaces configurable, explainable signals for research purposes.
No score represents a probability of profit unless explicitly validated
against out-of-sample data, and none of this constitutes financial advice.
