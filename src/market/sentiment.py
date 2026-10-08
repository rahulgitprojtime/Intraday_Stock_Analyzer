"""Market sentiment from NIFTY and BANK NIFTY — DECISIONS #31.

Decides which side of the market the scan and the paper strategies work:
BULLISH → rising stocks / long trades, BEARISH → falling stocks / short
trades, NEUTRAL (the indices disagree or are flat) → no new trades.

Per index, three votes from today's closed 1-min bars (+1 up, −1 down, 0
inside the dead band):
- day change: last vs the previous close (vs today's open when unknown),
- intraday: last vs today's open,
- trend: last vs the close `trend_minutes` ago (the open before that).
Index score = sum of votes (−3..+3). BULLISH when every available index
scores ≥ `min_index_score` and the total ≥ `min_total_score`; BEARISH
mirrored; otherwise NEUTRAL. No index data → NEUTRAL. Deterministic,
unvalidated thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"


@dataclass(frozen=True)
class SentimentConfig:
    dead_band_pct: float = 0.1       # |move| below this votes 0
    trend_minutes: int = 15
    min_index_score: int = 1         # every index must lean this way
    min_total_score: int = 3         # combined lean needed

    @classmethod
    def from_dict(cls, d: dict | None) -> SentimentConfig:
        d = d or {}
        return cls(float(d.get("dead_band_pct", 0.1)), int(d.get("trend_minutes", 15)),
                   int(d.get("min_index_score", 1)), int(d.get("min_total_score", 3)))


def _pct(a: float, b: float | None) -> float | None:
    return None if not b else (a / b - 1) * 100


def _vote(pct: float | None, band: float) -> int:
    if pct is None or abs(pct) < band:
        return 0
    return 1 if pct > 0 else -1


def index_view(closes: Sequence[float], day_open: float | None, prev_close: float | None,
               cfg: SentimentConfig) -> dict | None:
    """Votes for one index from today's closes (oldest first)."""
    if not closes or not day_open:
        return None
    last = closes[-1]
    ref = closes[-1 - cfg.trend_minutes] if len(closes) > cfg.trend_minutes else day_open
    day = _pct(last, prev_close if prev_close else day_open)
    intraday = _pct(last, day_open)
    trend = _pct(last, ref)
    votes = {"day": _vote(day, cfg.dead_band_pct), "intraday": _vote(intraday, cfg.dead_band_pct),
             "trend": _vote(trend, cfg.dead_band_pct)}
    return {"day_change_pct": round(day, 3) if day is not None else None,
            "intraday_pct": round(intraday, 3) if intraday is not None else None,
            "trend_pct": round(trend, 3) if trend is not None else None,
            "votes": votes, "score": sum(votes.values())}


def market_sentiment(indices: dict[str, tuple[Sequence[float], float | None, float | None]],
                     cfg: SentimentConfig = SentimentConfig()) -> dict:
    """`indices`: name → (today's closes, day open, previous close).
    Returns {"bias", "score", "indices": {name: view}, "nifty_change_pct"}."""
    views = {n: v for n, (closes, o, pc) in indices.items()
             if (v := index_view(closes, o, pc, cfg)) is not None}
    if not views:
        return {"bias": NEUTRAL, "score": 0, "indices": {}, "nifty_change_pct": None,
                "reason": "no index data"}
    scores = [v["score"] for v in views.values()]
    total = sum(scores)
    if all(s >= cfg.min_index_score for s in scores) and total >= cfg.min_total_score:
        bias = BULLISH
    elif all(s <= -cfg.min_index_score for s in scores) and total <= -cfg.min_total_score:
        bias = BEARISH
    else:
        bias = NEUTRAL
    nifty = views.get("NIFTY")
    return {"bias": bias, "score": total, "indices": views,
            "nifty_change_pct": nifty["day_change_pct"] if nifty else None}


def directions(bias: str | None) -> tuple[str, ...]:
    """Stock directions the scan looks for. NEUTRAL scans both (for the
    dashboard and research); strategies take no new trades then."""
    if bias == BULLISH:
        return ("LONG",)
    if bias == BEARISH:
        return ("SHORT",)
    return ("LONG", "SHORT")


def trade_direction(bias: str | None) -> str | None:
    """Direction the paper strategies may open: LONG, SHORT or None."""
    return {BULLISH: "LONG", BEARISH: "SHORT"}.get(bias or "")
