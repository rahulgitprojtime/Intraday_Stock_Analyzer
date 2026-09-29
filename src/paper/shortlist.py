"""Opening shortlist — DECISIONS #30. SIMULATION ONLY.

The playbook's screen ("a focused watchlist of 8-15 stocks by turnover, gap
percentage and volume, with liquidity filters") made concrete, from data
available when the observation window (09:15-09:30) ends:

- liquidity floor: turnover in the window >= `min_turnover` rupees;
- turnover in the window (rupees);
- |gap %| of today's open vs the prior close (in play either way);
- relative volume: window volume / the prior session's same window.

Each metric is turned into a percentile rank among the candidates (a
missing value counts as the median); the shortlist is the `top_n` by the
average rank. Sector momentum is NOT used: the source gives no rule and the
strategy context has no sector map. Starting values, not tuned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.data.models import SESSION_OPEN
from src.paper.orders import Bar


@dataclass(frozen=True)
class ShortlistConfig:
    top_n: int = 15
    window_minutes: int = 15
    min_turnover: float = 2e7            # Rs 2 crore traded in the window

    @classmethod
    def from_dict(cls, d: dict) -> ShortlistConfig:
        return cls(int(d.get("top_n", 15)), int(d.get("window_minutes", 15)),
                   float(d.get("min_turnover", 2e7)))


def _window(bars: list[Bar], minutes: int) -> list[Bar]:
    if not bars:
        return []
    end = datetime.combine(bars[0].ts.date(), SESSION_OPEN) + timedelta(minutes=minutes)
    return [b for b in bars if b.ts < end]


def _pct_rank(values: dict[str, float | None]) -> dict[str, float]:
    known = sorted(v for v in values.values() if v is not None)
    out = {}
    for sym, v in values.items():
        if v is None or len(known) < 2:
            out[sym] = 0.5
        else:
            out[sym] = sum(1 for k in known if k < v) / (len(known) - 1)
    return out


def opening_shortlist(today: dict[str, list[Bar]], prior: dict[str, list[Bar]],
                      cfg: ShortlistConfig) -> list[dict]:
    """Ranked rows (best first) for the symbols in `today` (1-min bars)."""
    rows: dict[str, dict] = {}
    for sym, bars in sorted(today.items()):
        win = _window(bars, cfg.window_minutes)
        if not win:
            continue
        turnover = sum(b.close * b.volume for b in win)
        if turnover < cfg.min_turnover:
            continue
        p = prior.get(sym) or []
        prev_vol = sum(b.volume for b in _window(p, cfg.window_minutes))
        rows[sym] = {
            "symbol": sym, "turnover": round(turnover, 2),
            "gap_pct": round((win[0].open / p[-1].close - 1) * 100, 4) if p else None,
            "rvol": round(sum(b.volume for b in win) / prev_vol, 4) if prev_vol else None,
        }
    ranks = [_pct_rank({s: r["turnover"] for s, r in rows.items()}),
             _pct_rank({s: None if r["gap_pct"] is None else abs(r["gap_pct"])
                        for s, r in rows.items()}),
             _pct_rank({s: r["rvol"] for s, r in rows.items()})]
    for sym, r in rows.items():
        r["score"] = round(sum(rk[sym] for rk in ranks) / len(ranks), 4)
    ordered = sorted(rows.values(), key=lambda r: (-r["score"], r["symbol"]))[:cfg.top_n]
    for i, r in enumerate(ordered, 1):
        r["rank"] = i
    return ordered
