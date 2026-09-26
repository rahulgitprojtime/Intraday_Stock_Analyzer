"""Stocks-in-play scanner — M5.

Scores how "in play" a stock is today (0-100) from time-of-day RVOL, gap
up %, ATR%, range expansion (today's range / daily ATR) and relative
strength vs NIFTY since the open. Only in-play stocks go on to setup
detection. Each feature maps linearly onto 0-100 between two bounds
(`ramps`), then blends by `weights`. Illustrative defaults, not tuned.
The score is a filter, not a probability of anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from src.data.models import Candle
from src.indicators.core import rvol_time_of_day
from src.quantitative.daily_prep import DailyPrep

_DEFAULT_WEIGHTS = {"rvol": 35, "gap": 15, "atr_pct": 15, "range_expansion": 15, "rs": 20}
_DEFAULT_RAMPS = {
    "rvol": (1.0, 3.0),              # x average cumulative volume by now
    "gap": (0.0, 3.0),               # gap up %, gap downs score 0
    "atr_pct": (1.0, 4.0),           # daily ATR as % of prior close
    "range_expansion": (0.3, 1.0),   # today's range / daily ATR
    "rs": (0.0, 2.0),                # % pts vs NIFTY since open
}


@dataclass(frozen=True)
class InPlayConfig:
    weights: dict = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    ramps: dict = field(default_factory=lambda: dict(_DEFAULT_RAMPS))
    min_score: float = 50.0
    min_rvol: float = 1.5

    @classmethod
    def from_dict(cls, d: dict) -> InPlayConfig:
        ramps = {k: tuple(v) for k, v in d.get("ramps", _DEFAULT_RAMPS).items()}
        return cls(
            weights=dict(d.get("weights", _DEFAULT_WEIGHTS)),
            ramps=ramps,
            min_score=float(d.get("min_score", 50.0)),
            min_rvol=float(d.get("min_rvol", 1.5)),
        )


@dataclass(frozen=True)
class InPlayResult:
    symbol: str
    score: float
    is_in_play: bool
    rvol: float | None = None
    gap_pct: float | None = None
    atr_pct: float | None = None
    range_expansion: float | None = None
    rs_pct: float | None = None
    components: dict = field(default_factory=dict)   # feature -> 0..100
    reason: str = ""


def _ramp(value: float | None, lo: float, hi: float) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100))


def score_in_play(
    symbol: str,
    today: Sequence[Candle],
    prep: DailyPrep,
    volume_curve: Sequence[float],
    index_today: Sequence[Candle],
    config: InPlayConfig | None = None,
) -> InPlayResult:
    """`today` / `index_today`: today's 1-min candles (forming bars ignored)."""
    cfg = config or InPlayConfig()
    bars = [c for c in today if c.is_complete]
    if not bars:
        return InPlayResult(symbol, 0.0, False, reason="no completed bars yet")
    idx = [c for c in index_today if c.is_complete]

    first, last = bars[0], bars[-1]
    rvol = rvol_time_of_day(bars, volume_curve)
    gap = (first.open / prep.prev_close - 1) * 100 if prep.prev_close else None
    day_range = max(c.high for c in bars) - min(c.low for c in bars)
    rng_x = day_range / prep.atr if prep.atr else None
    rs = None
    if first.open and idx and idx[0].open:
        rs = (last.close / first.open - 1) * 100 - (idx[-1].close / idx[0].open - 1) * 100

    features = {"rvol": rvol, "gap": gap, "atr_pct": prep.atr_pct,
                "range_expansion": rng_x, "rs": rs}
    components = {k: _ramp(features[k], *cfg.ramps[k]) for k in cfg.weights}
    score = sum(components[k] * w for k, w in cfg.weights.items()) / sum(cfg.weights.values())

    if rvol is None or rvol < cfg.min_rvol:
        in_play, reason = False, "RVOL below minimum"
    elif score < cfg.min_score:
        in_play, reason = False, "in-play score below minimum"
    else:
        top = sorted(components, key=components.get, reverse=True)[:2]
        in_play, reason = True, "driven by " + " + ".join(top)
    return InPlayResult(symbol, score, in_play, rvol, gap, prep.atr_pct, rng_x, rs,
                        components, reason)


def rank_in_play(results: Sequence[InPlayResult], top_n: int = 20) -> list[InPlayResult]:
    """In-play stocks only, highest score first."""
    return sorted((r for r in results if r.is_in_play), key=lambda r: -r.score)[:top_n]
