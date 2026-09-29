"""Initial stop and target — M10 (DECISIONS #20, #29).

Smallest deterministic method, fixed at entry and never moved:
risk = stop_atr_mult x daily ATR (prior sessions only), or
stop_fallback_pct of the fill when ATR is unavailable;
target = fill + target_r x risk. Returned as a broker `Bracket`, applied
to the actual fill price. Levels live only in the simulation, never on
recommendation cards (DECISIONS #11).
"""

from __future__ import annotations

from src.paper.orders import Bracket
from src.paper.policy import PaperConfig


def entry_bracket(daily_atr: float | None, cfg: PaperConfig) -> tuple[Bracket, str]:
    if daily_atr and daily_atr > 0:
        risk = cfg.stop_atr_mult * daily_atr
        return Bracket(round(risk, 4), round(cfg.target_r * risk, 4)), "atr"
    pct = cfg.stop_fallback_pct
    return Bracket(pct, round(cfg.target_r * pct, 4), pct=True), "pct_fallback"
