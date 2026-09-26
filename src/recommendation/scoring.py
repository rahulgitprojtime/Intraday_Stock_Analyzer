"""M6 baseline quantitative scoring model (spec §7, §13, §15).

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
    confluence_bonus: float
    confluence_max_extra: int
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
            confluence_bonus=float(e["confluence_bonus"]),
            confluence_max_extra=int(e["confluence_max_extra"]),
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


def setup_score(
    signals: Sequence[SetupSignal], cfg: EngineConfig
) -> tuple[float, SetupSignal | None, int]:
    """(score, best, confluence). `best` is None when no setup is present;
    it is the first FAILED signal when nothing scores above zero."""
    if not signals:
        return 0.0, None, 0
    pts = [cfg.state_points[s.state.value] for s in signals]
    top = max(pts)
    if top == 0:
        return 0.0, next((s for s in signals if s.state is SetupState.FAILED), None), 0
    best = signals[pts.index(top)]
    extra = sum(1 for s in signals if s is not best and s.state is SetupState.TRIGGERED)
    k = min(extra, cfg.confluence_max_extra)
    return min(100.0, top + cfg.confluence_bonus * k), best, k


def blend(components: Sequence[Component]) -> float | None:
    used = [c for c in components if c.status == AVAILABLE and c.weight and c.value is not None]
    total = sum(c.weight for c in used)
    if not used or total <= 0:
        return None
    return sum(c.weight * c.value for c in used) / total


def _cap(score: float, limit: float, name: str, reason: str, adj: list) -> float:
    if score > limit:
        adj.append(Adjustment(name, "cap", limit - score, reason))
        return limit
    return score


def apply_time_rules(
    score: float, mode: str, t: time, cfg: EngineConfig
) -> tuple[float, list[Adjustment]]:
    """Initial time-of-day heuristics (unvalidated until M10)."""
    adj: list[Adjustment] = []
    if cfg.lunch_start <= t < cfg.lunch_end:
        score -= cfg.lunch_penalty
        adj.append(Adjustment("lunch_lull", "penalty", -cfg.lunch_penalty,
                              "Lunch-hour penalty (initial time-of-day heuristic, unvalidated)"))
    score = max(0.0, min(100.0, score))
    watch_cap = cfg.categories["CANDIDATE"] - 0.01
    neutral_cap = cfg.categories["WATCH"] - 0.01
    if t < cfg.opening_cap_until:
        score = _cap(score, watch_cap, "opening_minutes",
                     "First minutes of the session: capped at WATCH (heuristic)", adj)
    if mode == "DAY" and t >= cfg.day_cap_after:
        score = _cap(score, watch_cap, "late_day",
                     "Late session for Day mode: capped at WATCH (heuristic)", adj)
    if t >= cfg.close_cap_after:
        score = _cap(score, neutral_cap, "near_close",
                     "Near the close: capped at NEUTRAL (heuristic)", adj)
    return score, adj


def categorize(score: float, cfg: EngineConfig) -> str:
    for name, floor in sorted(cfg.categories.items(), key=lambda kv: -kv[1]):
        if score >= floor:
            return name
    return "AVOID"
