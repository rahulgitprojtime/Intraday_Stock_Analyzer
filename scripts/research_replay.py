"""Record + label research snapshots over saved history — M14 (DECISIONS #24).

Runs the worker on every replay day (the first 20 only feed the daily prep),
records snapshots, labels outcomes from the same bars. Days already
labeled are skipped, so it can be stopped and resumed.

    python scripts/research_replay.py --replay data/replay_1y --jobs 4
    python scripts/research_replay.py --replay data/universe_1y --scan-universe \
        --out data/research/universe --jobs 4          # whole market (M16)

Caveats: data/replay_1y is the curated 25 large caps only (survivorship);
data/universe_1y is today's listed stocks (delisted ones missing). No
historical news, depth or ticks, so those questions stay live-only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.label_outcomes import bar_loader  # noqa: E402
from src.research.outcomes import label_file  # noqa: E402

OUT = ROOT / "data" / "research" / "replay"


def run_day(replay: Path, day: str, out: Path, panel: int, scan: bool = False) -> str:
    labeled = out / "labeled" / f"{day}.jsonl"
    if labeled.exists():
        return f"{day} skip (done)"
    snaps = out / "snapshots" / f"{day}.jsonl"
    snaps.unlink(missing_ok=True)                 # a half-written day is redone, not appended
    r = subprocess.run([sys.executable, "-m", "src.app.worker", "--replay", str(replay),
                        "--day", day, "--speed", "0", "--out", str(out / "state" / f"{day}.json"),
                        "--snapshots", str(out / "snapshots"), "--panel-minutes", str(panel)]
                       + (["--scan-universe"] if scan else []),
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or not snaps.exists():
        return f"{day} FAIL worker: {(r.stderr or r.stdout).strip().splitlines()[-1:]}"
    n, ok = label_file(snaps, bar_loader(replay), labeled)
    return f"{day} {n} rows, {ok} labeled"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--replay", type=Path, default=ROOT / "data" / "replay_1y")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--skip", type=int, default=20, help="first N days only feed the prep")
    p.add_argument("--panel-minutes", type=int, default=15)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--scan-universe", action="store_true",
                   help="whole-market replay: top N per minute from <replay>/pools.json (M16)")
    a = p.parse_args(argv)
    days = sorted(d.name for d in a.replay.iterdir() if d.is_dir() and d.name[:2] == "20")[a.skip:]
    print(f"{len(days)} days {days[0]}..{days[-1]} -> {a.out}", flush=True)
    fails = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for msg in ex.map(lambda d: run_day(a.replay, d, a.out, a.panel_minutes, a.scan_universe), days):
            fails += "FAIL" in msg
            print(msg, flush=True)
    print(f"done: {len(days) - fails}/{len(days)} days")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
