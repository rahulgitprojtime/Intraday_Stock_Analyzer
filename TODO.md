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
- [ ] Trend: strongest-first (#32) let 3 wide-stop positions hold every slot for hours on 8 Oct — test a slot/time rule on the validate split, not on 8 Oct
- [ ] Scalp: ~₹55 charges per round trip vs a 1.5R target on 1-min stops — 8 of 26 target hits lost net on 8 Oct; test a minimum stop/target size vs costs (pre-register)
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
