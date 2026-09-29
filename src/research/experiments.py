"""Experiment helpers — M17 (DECISIONS #27).

`reblend` re-scores a recorded snapshot row with different group weights
(the groups are stored per row, so a weight change needs no replay).
`paired_ic` compares two rankings on the same rows: per day, the rank
correlation of each with the forward outcome; then the mean and t-stat of
the daily differences (days are the independent units).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable

from src.research.evaluate import spearman


def reblend(row: dict, weights: dict[str, float]) -> float | None:
    used = [(w, row.get(f"g_{g}")) for g, w in weights.items() if row.get(f"g_{g}") is not None]
    total = sum(w for w, _ in used)
    return sum(w * v for w, v in used) / total if total else None


def paired_ic(rows: list[dict], a: Callable[[dict], float | None],
              b: Callable[[dict], float | None], metric: str):
    """(mean IC(a) - IC(b), t over days, days, mean IC(a), mean IC(b))."""
    per_day = defaultdict(lambda: ([], [], []))
    for r in rows:
        xa, xb, y = a(r), b(r), r.get(metric)
        if xa is not None and xb is not None and y is not None:
            d = per_day[r["day"]]
            d[0].append(xa), d[1].append(xb), d[2].append(y)
    ics_a, ics_b, diffs = [], [], []
    for xa, xb, y in per_day.values():
        ia, ib = spearman(xa, y), spearman(xb, y)
        if ia is not None and ib is not None:
            ics_a.append(ia), ics_b.append(ib), diffs.append(ia - ib)
    if not diffs:
        return None, None, 0, None, None
    m = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    t = m / sd * len(diffs) ** 0.5 if sd > 0 else None
    return m, t, len(diffs), statistics.fmean(ics_a), statistics.fmean(ics_b)
