"""Answer the pre-registered research questions from labeled snapshots — M14 (DECISIONS #24).

    python scripts/evaluate.py --labeled data/research/live/labeled --out reports/live.md
    python scripts/evaluate.py --labeled data/research/replay/labeled --to 2026-06-30   # explore split
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.research.evaluate import load_rows, report  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labeled", type=Path, required=True)
    p.add_argument("--from", dest="start", default=None, help="first day YYYY-MM-DD")
    p.add_argument("--to", dest="end", default=None, help="last day YYYY-MM-DD")
    p.add_argument("--version", default=None, help="only this strategy_version")
    p.add_argument("--title", default="Research evaluation")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    rows = load_rows(a.labeled, a.start, a.end, a.version)
    if not rows:
        print("no labeled panel rows")
        return 1
    text = report(rows, a.title)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
        print(f"report -> {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
