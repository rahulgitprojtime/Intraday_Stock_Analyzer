"""Paper-trading replay — M10a (DECISIONS #20). SIMULATION ONLY.

Replays real sessions minute by minute through the *existing* worker tick
(same recommendations as the dashboard) and feeds the ranked list to the
paper simulator. Writes an append-only journal and a daily report per day:

    python scripts/paper_replay.py --replay data/replay --days 2026-09-25
    python scripts/paper_replay.py --replay data/replay --all

Outputs: reports/replay/<run_id>/journal/<day>.jsonl and
reports/replay/<run_id>/<day>/{paper_trades.json, daily_report.json, .md}.
Historical news is not available in replay: news shows NOT_AVAILABLE.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.sources import ReplaySource  # noqa: E402
from src.app.worker import base_context, prepare, replay_clock, run_tick  # noqa: E402
from src.market.sector import load_sector_map  # noqa: E402
from src.paper.journal import Journal, version_info  # noqa: E402
from src.paper.policy import PaperConfig  # noqa: E402
from src.paper.report import write_daily_report  # noqa: E402
from src.paper.simulator import PaperSimulator  # noqa: E402
from src.utils.config import load_universe, load_yaml  # noqa: E402


def run_paper_day(replay_root: str | Path, day: date, out_root: str | Path, cfg: PaperConfig,
                  run_id: str) -> dict:
    sectors, _ = load_sector_map(load_universe().get("symbols") or [])
    source = ReplaySource(replay_root, day, {s["index"] for s in sectors.values()})
    stocks, index = source.instruments()
    ctx = base_context(source, stocks, index, "replay", source.is_demo)
    ctx.sector_indices = source.sector_indices()
    prepare(ctx, day)
    atr = {sym: (pr.prep.atr if pr else None) for sym, pr in ctx.preps.items()}
    versions = version_info()
    run_dir = Path(out_root) / run_id
    journal_path = run_dir / "journal" / f"{day.isoformat()}.jsonl"
    if journal_path.exists():
        journal_path.unlink()          # a replay run regenerates its own journal from scratch
    journal = Journal(journal_path)
    sim = PaperSimulator(cfg, journal, run_id, versions, "replay")
    for as_of in replay_clock(day):
        state = run_tick(ctx, as_of, as_of)        # generated_at = as_of: deterministic
        bars = {s.trading_symbol: source.minute_candles(s, as_of) for s in stocks}
        sim.step(as_of, state["modes"][cfg.mode], bars, atr)
    sim.finish()
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
    a = p.parse_args(argv)
    cfg = PaperConfig.from_dict(load_yaml("paper.yaml"))
    days = sorted(a.days)
    if a.all:
        all_days = sorted(date.fromisoformat(d.name) for d in a.replay.iterdir() if d.is_dir())
        days = all_days[20:]
    if not days:
        p.error("give --days or --all")
    run_id = a.run_id or f"{version_info()['strategy_version']}-{version_info()['config_hash']}"
    for d in days:
        res = run_paper_day(a.replay, d, a.out, cfg, run_id)
        closed = [t for t in res["trades"] if t["exit_reason"]]
        net = sum(t["net_pnl"] for t in closed)
        print(f"{d}  trades={len(closed)} missed={len(res['missed'])} net={net:+.2f}  "
              f"-> {res['report_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
