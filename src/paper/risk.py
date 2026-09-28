"""Initial stop and target — M10 (DECISIONS #20).

Smallest deterministic method, fixed at entry and never moved:
risk = stop_atr_mult x daily ATR (prior sessions only), or
stop_fallback_pct of the fill when ATR is unavailable;
target = fill + target_r x risk. Levels live only in the simulation
journal, never on recommendation cards (DECISIONS #11).
"""

from __future__ import annotations

from src.paper.policy import PaperConfig


def initial_levels(fill: float, daily_atr: float | None, cfg: PaperConfig
                   ) -> tuple[float, float, str]:
    if daily_atr and daily_atr > 0:
        risk, method = cfg.stop_atr_mult * daily_atr, "atr"
    else:
        risk, method = fill * cfg.stop_fallback_pct / 100, "pct_fallback"
    return round(fill - risk, 4), round(fill + cfg.target_r * risk, 4), method
