"""Paper-trading replay — M10a (DECISIONS #20, #29). SIMULATION ONLY.

Replays real sessions minute by minute through the *existing* worker tick
(same recommendations as the dashboard); `RecommendationStrategy` turns the
ranked list into orders on the simulated `BacktestBroker` (next-bar fills,
full cost model, 15:15 square-off). Writes an append-only journal, a daily
report and the SQLite ledger (one run per day):

    python scripts/paper_replay.py --replay data/replay --days 2026-09-25
    python scripts/paper_replay.py --replay data/replay --all

Outputs: reports/replay/<run_id>/journal/<day>.jsonl and
reports/replay/<run_id>/<day>/{paper_trades.json, daily_report.json, .md}.
Historical news is not available in replay: news shows NOT_AVAILABLE.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.sources import ReplaySource  # noqa: E402
from src.app.worker import base_context, prepare, replay_clock, run_tick  # noqa: E402
from src.market.sector import load_sector_map  # noqa: E402
from src.paper.broker import BacktestBroker, BrokerConfig  # noqa: E402
from src.paper.costs import CostModel  # noqa: E402
from src.paper.journal import Journal, version_info  # noqa: E402
from src.paper.orders import Bar  # noqa: E402
from src.paper.policy import PaperConfig  # noqa: E402
from src.paper.report import write_daily_report  # noqa: E402
from src.paper.session import TradingSession  # noqa: E402
from src.paper.strategies.recommendation import RecommendationStrategy  # noqa: E402
from src.utils.config import load_universe, load_yaml  # noqa: E402


def run_paper_day(replay_root: str | Path, day: date, out_root: str | Path, cfg: PaperConfig,
                  run_id: str, ledger=None) -> dict:
    sectors, _ = load_sector_map(load_universe().get("symbols") or [])
    source = ReplaySource(replay_root, day,
                          {s["index"] for s in sectors.values()} | {"BANKNIFTY"})
    stocks, index = source.instruments()
    ctx = base_context(source, stocks, index, "replay", source.is_demo)
    ctx.sector_indices = source.sector_indices()
    ctx.bank_index = next((i for i in ctx.sector_indices if i.trading_symbol == "BANKNIFTY"),
                          None)
    prepare(ctx, day)
    atr = {sym: (pr.prep.atr if pr else None) for sym, pr in ctx.preps.items()}
    versions = version_info()
    run_dir = Path(out_root) / run_id
    journal_path = run_dir / "journal" / f"{day.isoformat()}.jsonl"
    if journal_path.exists():
        journal_path.unlink()          # a replay run regenerates its own journal from scratch
    journal = Journal(journal_path)
    # generated_at = as_of: deterministic recommendations for the minute
    recs_at = lambda as_of: run_tick(ctx, as_of, as_of)["modes"][cfg.mode]  # noqa: E731
    strategy = RecommendationStrategy(cfg, recs_at, atr, journal, run_id, versions, "replay")
    raw = load_yaml("paper.yaml")
    broker_cfg = BrokerConfig.from_dict(raw["broker"])
    ledger_run = f"{run_id}:{day.isoformat()}"
    if ledger is not None:
        ledger.start_run(ledger_run, "REPLAY", strategy.name, broker_cfg.starting_capital,
                         strategy.params())
    broker = BacktestBroker(broker_cfg, CostModel.from_dict(load_yaml("costs.yaml")), ledger,
                            ledger_run)
    session = TradingSession(strategy, broker, ledger=ledger)
    session.start_day(day)
    seen: dict[str, datetime] = {}
    for as_of in replay_clock(day):
        new = []
        for s in stocks:
            for c in source.minute_candles(s, as_of):       # only bars closed by as_of
                if c.timestamp > seen.get(s.trading_symbol, datetime.min):
                    new.append(Bar.from_candle(c))
                    seen[s.trading_symbol] = c.timestamp
        session.step(as_of, new)
    session.end_day(day)
    report_dir = write_daily_report(run_dir, day.isoformat(), journal.trades(), journal.missed(),
                                    versions | {"replay_or_live": "replay", "run_id": run_id,
                                                "mode": cfg.mode, "demo": source.is_demo})
    return {"journal": journal_path, "report_dir": report_dir, "trades": journal.trades(),
            "missed": journal.missed()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--replay", type=Path, required=True)
    p.add_argument("--days", nargs="*", type=date.fromisoformat, default=[])
    p.add_argument("--all", action="store_true", help="every day that has 20 prior sessions")
    p.add_argument("--out", type=Path, default=Path("reports/replay"))
    p.add_argument("--run-id", default=None)
    p.add_argument("--ledger", type=Path, default=None,
                   help="SQLite ledger (default paper.yaml backtest_ledger)")
    a = p.parse_args(argv)
    raw = load_yaml("paper.yaml")
    cfg = PaperConfig.from_dict(raw)
    days = sorted(a.days)
    if a.all:
        all_days = sorted(date.fromisoformat(d.name) for d in a.replay.iterdir() if d.is_dir())
        days = all_days[20:]
    if not days:
        p.error("give --days or --all")
    run_id = a.run_id or f"{version_info()['strategy_version']}-{version_info()['config_hash']}"
    from src.paper.ledger import Ledger
    ledger = Ledger(a.ledger or raw["backtest_ledger"])
    for d in days:
        res = run_paper_day(a.replay, d, a.out, cfg, run_id, ledger)
        closed = [t for t in res["trades"] if t["exit_reason"]]
        net = sum(t["net_pnl"] for t in closed)
        print(f"{d}  trades={len(closed)} missed={len(res['missed'])} net={net:+.2f}  "
              f"-> {res['report_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
