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

## Configuration

All tunable behavior lives in `config/*.yaml`, not in code:
- `settings.yaml` — storage, feed, candle timeframes.
- `strategy.yaml` — recommendation weights/categories, indicator params.
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
