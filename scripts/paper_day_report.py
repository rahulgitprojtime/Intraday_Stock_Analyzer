"""Write the paper-trading day report (DECISIONS #30): reports/paper_<day>.md.

    python scripts/paper_day_report.py                 # today
    python scripts/paper_day_report.py --day 2026-10-09
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.paper.day_report import write_day_report  # noqa: E402
from src.utils.config import load_yaml  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--day", type=date.fromisoformat, default=date.today())
    p.add_argument("--ledger", type=Path, default=None, help="default: paper.yaml ledger")
    p.add_argument("--out-dir", type=Path, default=Path("reports"))
    a = p.parse_args(argv)
    ledger = a.ledger or Path(load_yaml("paper.yaml")["ledger"])
    out = write_day_report(ledger, a.day, a.out_dir)
    print(out.read_text(encoding="utf-8"))
    print(f"Written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
