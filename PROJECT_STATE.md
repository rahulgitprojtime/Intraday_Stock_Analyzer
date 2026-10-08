# PROJECT_STATE.md

Last updated: 2026-10-08. Decisions: `DECISIONS.md`. Open work: `TODO.md`.

## Product
- Live intraday **recommendation** dashboard for NSE cash equities on the
  Groww Trading API (market data only), plus a **paper-trading and
  backtesting simulator**. No order ever reaches Groww (DECISIONS #8).
- Recommendations: LONG-only momentum candidates, SCALP (1-min) and DAY
  (5/15-min), no price levels on cards (#11).
- Paper trading (#29-#31): market sentiment (NIFTY + BANK NIFTY) picks the
  side; `scalp` (1-min price action, 15-min time exit) and `trend` (5-min
  setups confirmed by indicators) trade only on a clear setup, long in a
  bullish market, short in a bearish one, nothing when neutral. Stops and
  targets on every position, net P&L after Groww charges.

## How a market day runs (Windows Task Scheduler, `scripts/live_day.ps1`)
1. 08:40 `worker --paper` (relaunched every 5 min until 15:25 if it dies;
   a relaunch resumes the paper positions): daily stats for the whole
   market → each minute: sentiment → scan (every stock moving with the
   market on unusual volume, up to the API budget) → score →
   `data/processed/state.json` → dashboard; research snapshots; paper
   strategies trade on live ticks; square-off 15:15.
2. 15:45 label phase: `reports/paper_<day>.md`, research labels and
   `reports/live_<day>.md`.

## Built (all milestones done)
| Area | What | Decisions |
|---|---|---|
| Groww adapter | auth, quotes/LTP/OHLC, 1-min + daily history, instrument master, live feed + watchdog | #1 #4 #9 #10 #17 |
| Data | 1-min CSV cache, daily prep, 3/5/15-min resampling | #12 |
| Signals | indicators, 8 named setups, in-play features, microstructure | #13 #17 |
| Universe | sentiment-led market-wide scan, no fixed count, price band 250-2500 | #21 #23 #26 #31 |
| Scoring | group score (setup 25, volume 20, movement 15, momentum 15, sector 10, market 10, liquidity 5), confluence, time rules, hard rejects, sector verdict, news checklist | #14 #15 #18 #19 #22 #25 |
| Dashboard | Streamlit reads state.json; Paper trading page reads the ledgers | #8 |
| Research | snapshots → outcomes → pre-registered evaluation; whole-market history | #24 #26 #27 #28 |
| Simulator | broker (long/short, brackets, costs, limits, square-off, resume), scalp + trend (live; same-bar signals ranked strongest first; shared entry/exit rules #33), ORB + recommendation (backtests), SQLite ledger, day report | #20 #29 #30 #31 #32 #33 |

## What the data says so far
- Whole-market explore (155 days): among the day's movers the most extended
  slightly mean-revert; score, RVOL, volume and momentum rank negatively;
  nothing clears the 0.1% round-trip cost (#27).
- Validation: reweighting away from extension and a pullback-only list both
  failed; the lunch penalty was removed (strategy 2026-09-29.m16).
- ORB long-only backtest (83 days, 25 stocks): −₹29,259 on ₹1 lakh after
  ₹18,023 charges (#29). No strategy has a demonstrated edge yet.
- #33 rules on the validate split (44 days): scalp −₹86,732 (was −₹2,18,986),
  trend −₹41,568 (was −₹48,210); both lose before charges — no edge yet.
- 8 Oct whole-market backtest (bearish day, shorts only, ranked #32): scalp
  −₹8,360 on 113 trades (charges ₹6,233 > gross loss); trend −₹959 on 6
  trades — strongest movers' wide stops held all 3 slots for hours.

## Known issues
- Worker exits on network loss (DNS) and relaunches fail at login until
  the network returns; feed disconnects have coincided with console kills
  (29 Sep). Paper positions survive restarts (#30); research rows for the
  gap are lost.
- The laptop must stay on and plugged in during market hours.
- Sector map covers only the curated names; scanned stocks show sector
  "not available".
- Dependencies are not pinned (#2).

## Environment
Windows laptop, `.venv`, Python 3.13; core code is stdlib-only (Windows
Application Control blocked pandas/pyarrow DLLs at first; unblocked for the
dashboard). Tests: `pytest` (adapter tests use `tests/fakes/fake_groww.py`).
