"""Scoring configuration (DECISIONS #22, #25).

Values come from `strategy.yaml` `engine:` — temporary engineering
defaults, NOT claimed optimal; M10 validates them. The blend runs over
available components with a configured weight only, so an unavailable or
not-yet-weighted component never changes the score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time

from src.quantitative.setups import SetupSignal, SetupState
from src.recommendation.models import AVAILABLE, Adjustment, Component


@dataclass(frozen=True)
class EngineConfig:
    weights: dict
    state_points: dict
    confluence_bonus: dict    # families active -> bonus points
    confluence_max_bonus: float
    market_ramp_pct: tuple
    ext_atr_mult: dict
    ext_fallback_pct: float
    opening_cap_until: time
    lunch_start: time
    lunch_end: time
    lunch_penalty: float
    day_cap_after: time
    close_cap_after: time
    categories: dict          # category -> min score (recommendation.categories)

    @classmethod
    def from_strategy(cls, strategy: dict) -> EngineConfig:
        e, t = strategy["engine"], strategy["engine"]["time_rules"]
        return cls(
            weights=dict(e["weights"]),
            state_points=dict(e["state_points"]),
            confluence_bonus={int(k): float(v) for k, v in e["confluence_bonus"].items()},
            confluence_max_bonus=float(e["confluence_max_bonus"]),
            market_ramp_pct=tuple(e["market_ramp_pct"]),
            ext_atr_mult=dict(e["ext_atr_mult"]),
            ext_fallback_pct=float(e["ext_fallback_pct"]),
            opening_cap_until=time.fromisoformat(t["opening_cap_until"]),
            lunch_start=time.fromisoformat(t["lunch_start"]),
            lunch_end=time.fromisoformat(t["lunch_end"]),
            lunch_penalty=float(t["lunch_penalty"]),
            day_cap_after=time.fromisoformat(t["day_cap_after"]),
            close_cap_after=time.fromisoformat(t["close_cap_after"]),
            categories=dict(strategy["recommendation"]["categories"]),
        )


# Independent evidence families (DECISIONS #15). Setups in one family are
# correlated and count once toward confluence.
SETUP_FAMILIES = {
    "ORB5": "price_structure", "ORB15": "price_structure", "PDH": "price_structure",
    "GAP_AND_GO": "price_structure", "NARROW_CPR": "price_structure",
    "VWAP_RECLAIM": "vwap", "VWAP_PULLBACK": "vwap",
    "EMA_PULLBACK": "trend",
    "MOMENTUM_BURST": "momentum",
    "RS_VS_NIFTY": "relative_strength",
}
_ACTIVE = (SetupState.TRIGGERED, SetupState.FORMING)


def setup_score(
    signals: Sequence[SetupSignal], cfg: EngineConfig
) -> tuple[float, SetupSignal | None]:
    """(points of the best setup, best). `best` is None when no setup is
    present; it is the first FAILED signal when nothing scores above zero."""
    if not signals:
        return 0.0, None
    pts = [cfg.state_points[s.state.value] for s in signals]
    top = max(pts)
    if top == 0:
        return 0.0, next((s for s in signals if s.state is SetupState.FAILED), None)
    return float(top), signals[pts.index(top)]


def confluence(signals: Sequence[SetupSignal], cfg: EngineConfig) -> tuple[tuple[str, ...], float]:
    """(active families, bonus). Bonus from the configured table for the
    number of distinct active families, capped at `confluence_max_bonus`."""
    families = tuple(sorted({SETUP_FAMILIES.get(s.name, s.name)
                             for s in signals if s.state in _ACTIVE}))
    eligible = [pts for n, pts in cfg.confluence_bonus.items() if len(families) >= n]
    return families, min(cfg.confluence_max_bonus, max(eligible, default=0.0))


def blend(components: Sequence[Component]) -> float | None:
    used = [c for c in components if c.status == AVAILABLE and c.weight and c.value is not None]
    total = sum(c.weight for c in used)
    if not used or total <= 0:
        return None
    return sum(c.weight * c.value for c in used) / total


def cap_score(score: float, limit: float, name: str, reason: str, adj: list) -> float:
    if score > limit:
        adj.append(Adjustment(name, "cap", limit - score, reason))
        return limit
    return score


def apply_time_rules(
    score: float, mode: str, t: time, cfg: EngineConfig
) -> tuple[float, list[Adjustment]]:
    """Initial time-of-day heuristics (unvalidated until M10)."""
    adj: list[Adjustment] = []
    if cfg.lunch_penalty > 0 and cfg.lunch_start <= t < cfg.lunch_end:
        score -= cfg.lunch_penalty
        adj.append(Adjustment("lunch_lull", "penalty", -cfg.lunch_penalty,
                              "Lunch-hour penalty (initial time-of-day heuristic, unvalidated)"))
    score = max(0.0, min(100.0, score))
    watch_cap = cfg.categories["CANDIDATE"] - 0.01
    neutral_cap = cfg.categories["WATCH"] - 0.01
    if t < cfg.opening_cap_until:
        score = cap_score(score, watch_cap, "opening_minutes",
                     "First minutes of the session: capped at WATCH (heuristic)", adj)
    if mode == "DAY" and t >= cfg.day_cap_after:
        score = cap_score(score, watch_cap, "late_day",
                     "Late session for Day mode: capped at WATCH (heuristic)", adj)
    if t >= cfg.close_cap_after:
        score = cap_score(score, neutral_cap, "near_close",
                     "Near the close: capped at NEUTRAL (heuristic)", adj)
    return score, adj


def categorize(score: float, cfg: EngineConfig) -> str:
    for name, floor in sorted(cfg.categories.items(), key=lambda kv: -kv[1]):
        if score >= floor:
            return name
    return "AVOID"
