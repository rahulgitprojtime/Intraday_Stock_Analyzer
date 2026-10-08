# TODO.md

Open tasks only; finished work is in `PROJECT_STATE.md` and `DECISIONS.md`.

## Live operations
- [ ] 2026-10-09 first day of sentiment-led scalp + trend: check the sentiment and universe size in `data/scans/`, `logs/worker_*.log`, `reports/paper_2026-10-09.md`
- [ ] Worker must survive network loss: retry auth/REST with backoff instead of exiting (29 Sep: DNS failure at 12:24, relaunches failed at login until 13:45)
- [ ] Find the cause of the 0xC000013A kills right after NATS feed disconnects ("nats: unexpected EOF", 09:12 and 09:28 on 29 Sep)
- [ ] Retry daily prep on truncated Groww JSON responses (AARTIPHARM, 29 Sep)
- [ ] Observe a full session end to end: watchdog restart on a real disconnect, REST refresh latency, one scan cycle's duration and call count at the open
- [ ] Pin dependency versions (growwapi 1.5.0, nats-py) — DECISIONS #2

## Paper trading
- [ ] Paper fills for symbols without live ticks (fall back to bars)
- [ ] Backtest scalp + trend over `data/universe_1y` (needs NIFTY + BANKNIFTY bars for the sentiment); tune nothing on the full set (#24 splits)
- [ ] Entry quality on the explore split: which scalp/trend setups and conditions have any gross edge before costs (#33 validate: both lose before charges)
- [ ] Scalp: 15-min time exit vs 2R target + noise-floor stop — 332 of 699 validate scalps timed out; explore hold time / target together
- [ ] Report generator (scratch reportlab script) → scripts/backtest_report.py if PDFs stay wanted (reportlab is not a project dependency)
- [ ] Check how often sentiment is NEUTRAL (no trades) and how large the universe gets at the open
- [ ] Run `paper_replay --all` on `data/replay_1y` with the simulated broker (recommendation baseline)
- [ ] Multi-day paper report: equity curve, drawdown, breakdowns with sample sizes

## Research (pre-registered rules, DECISIONS #24)
- [ ] Run exploration round 2 (#28) on the explore split; anything promising is pre-registered, then validate, then test
- [ ] After ≥ 20 live days: live report; proposed changes tested on validate then test
- [ ] Decide whether the recommendation engine gets a short side (needs its own pre-registered tests)

## Data quality
- [ ] Sector map for scanned stocks (NSE index constituents); verify NIFTYCDTY membership for RELIANCE/ONGC/NTPC/POWERGRID/ULTRACEMCO/ADANIENT
- [ ] Review misclassified news headlines weekly; extend `config/news.yaml`
- [ ] Optional: keyed news source with snippets (Marketaux / Drishti) behind `NewsSource`
- [ ] Optional: exchange-wide breadth, INDIAVIX regime
