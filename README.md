# Intraday Scanner

Live intraday **stock recommendation dashboard** for Indian cash equities,
using the Groww Trading API as a market-data source. Ranks liquid intraday
candidates by a blended, explainable Recommendation Score (quantitative +
qualitative + market/sector context + liquidity). **It never places real
orders** (paper trading and backtests use a simulated broker only) **and
the score is not a probability of profit.**

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
python scripts/scan_now.py --top 25   # market-wide volume scan, once (read-only)
python -m src.app.worker              # start by ~09:00: daily stats for 1,643 stocks first
```

With `scan.enabled` in `config/universe.yaml` (default), the live universe
is the top 25 by volume change across all NSE EQ intraday stocks, re-picked
every minute; the curated `symbols` list is used only by replays.

`INTRADAY_STATE=<path> streamlit run app/dashboard.py --server.port 8502`
points a second dashboard at another state file (e.g. a replay next to
live).

Live mode streams LTP/depth for the whole universe. The dashboard header
shows the feed status (LIVE / STALE / DOWN / OFF); on STALE/DOWN the
worker keeps ranking from REST candles and pauses the spread check and
Scalp microstructure.

`--speed` is simulated minutes per real minute (`0` = as fast as
possible). DEMO data is synthetic and is not strategy evidence.

## Paper trading and backtests (simulation only)

Strategies trade against an in-process simulated broker; nothing is ever
sent to Groww (DECISIONS #29). Fills: next bar / next live tick plus
slippage, limits only when traded through; Groww intraday charges from
`config/costs.yaml`; square-off 15:15. Results land in SQLite ledgers
under `data/paper/` and on the dashboard's **Paper trading** page.

```bash
# Backtest the sample Opening Range Breakout on cached 1-min candles
python scripts/backtest.py --strategy orb --cache data/replay_1y --from 2026-06-01 --to 2026-09-25
# ...downloading missing days once (read-only history, needs .env)
python scripts/backtest.py --strategy orb --symbols RELIANCE INFY --from 2026-09-01 --to 2026-09-25 --cache data/replay_1y --fetch
# Intraday playbook: opening shortlist + ORB / VWAP / EMA 5-15 / Bollinger / PDL setups
python scripts/backtest.py --strategy playbook --cache data/replay_1y --from 2025-10-03 --to 2026-09-28
python scripts/backtest.py --strategy playbook --setups VWAP --cache data/replay_1y --from 2025-10-03 --to 2026-09-28
# Replay the recommendation engine as a strategy (journal + daily report + ledger)
python scripts/paper_replay.py --replay data/replay --days 2026-09-25
# Live paper trading during market hours (strategy from config/paper.yaml)
python -m src.app.worker --paper
```

## Configuration

Sector confirmation (M8) uses `config/sectors.yaml`: sector → NSE index +
member stocks. Keep it in sync with `universe.yaml`; a startup check
reports members outside the universe.

All tunable behavior lives in `config/*.yaml`, not in code:
- `settings.yaml` — storage, feed, candle timeframes.
- `strategy.yaml` — M6 engine baseline weights, time heuristics, categories,
  in-play scanner, indicator params.
- `universe.yaml` — symbol universe and liquidity filters.
- `paper.yaml` — simulated broker (capital, limits, slippage, square-off),
  paper strategy, recommendation entry policy, ORB parameters, ledgers.
- `costs.yaml` — brokerage, STT, exchange, SEBI, IPFT, GST, stamp duty.

## Documentation map

- `ARCHITECTURE.md` — module layout and hard boundaries between layers.
- `DECISIONS.md` — why things are built the way they are.
- `docs/groww_api_notes.md` — verified (not assumed) Groww API facts.
- `CLAUDE.md` — working agreement for AI-assisted sessions on this repo.

## Disclaimer

This tool surfaces configurable, explainable signals for research purposes.
No score represents a probability of profit unless explicitly validated
against out-of-sample data, and none of this constitutes financial advice.
