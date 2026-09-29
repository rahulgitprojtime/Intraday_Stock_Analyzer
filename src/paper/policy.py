"""Paper-trading entry policy — M10 (DECISIONS #20).

Decides whether an existing recommendation becomes a virtual entry. It
only reads recommendation fields; it never re-scores. Every rejection of
a top-listed candidate returns a reason, so missed signals are recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

CATEGORY_RANK = {"STRONG_CANDIDATE": 4, "CANDIDATE": 3, "WATCH": 2, "NEUTRAL": 1, "AVOID": 0}


@dataclass(frozen=True)
class PaperConfig:
    enabled: bool
    mode: str
    quantity: int
    minimum_category: str
    minimum_score: float
    require_triggered_setup: bool
    require_in_play: bool
    max_rank: int
    max_open_positions: int
    max_trades_per_symbol_per_day: int
    no_entry_after: time
    stop_atr_mult: float
    stop_fallback_pct: float
    target_r: float

    @classmethod
    def from_dict(cls, d: dict) -> PaperConfig:
        e, r = d["entry"], d["risk"]
        return cls(
            enabled=bool(d.get("enabled", False)), mode=str(d["mode"]),
            quantity=int(d["quantity"]),
            minimum_category=str(e["minimum_category"]), minimum_score=float(e["minimum_score"]),
            require_triggered_setup=bool(e["require_triggered_setup"]),
            require_in_play=bool(e["require_in_play"]), max_rank=int(e["max_rank"]),
            max_open_positions=int(e["max_open_positions"]),
            max_trades_per_symbol_per_day=int(e["max_trades_per_symbol_per_day"]),
            no_entry_after=time.fromisoformat(e["no_entry_after"]),
            stop_atr_mult=float(r["stop_atr_mult"]), stop_fallback_pct=float(r["stop_fallback_pct"]),
            target_r=float(r["target_r"]),
        )


def eligibility(rec: dict, cfg: PaperConfig, open_symbols: set, traded_today: dict,
                at: time) -> str | None:
    """None if the recommendation qualifies for a paper entry, else the reason."""
    if not rec.get("eligible_for_top_n") or rec.get("rank") is None:
        return "not rankable (AVOID)"
    if rec["rank"] > cfg.max_rank:
        return f"rank {rec['rank']} outside top {cfg.max_rank}"
    if CATEGORY_RANK.get(rec["category"], 0) < CATEGORY_RANK[cfg.minimum_category]:
        return f"category {rec['category']} below {cfg.minimum_category}"
    if rec["score"] < cfg.minimum_score:
        return f"score {rec['score']:.1f} below {cfg.minimum_score:g}"
    if cfg.require_triggered_setup and rec["setup"].get("best_state") != "TRIGGERED":
        return "best setup not TRIGGERED"
    if cfg.require_in_play and not rec["quantitative"].get("is_in_play"):
        return "not in play"
    if rec["data_quality"]["status"] != "OK":
        return f"data {rec['data_quality']['status']}"
    if at > cfg.no_entry_after:
        return f"after no_entry_after {cfg.no_entry_after:%H:%M}"
    if rec["symbol"] in open_symbols:
        return "position already open"
    if traded_today.get(rec["symbol"], 0) >= cfg.max_trades_per_symbol_per_day:
        return "symbol traded today"
    if len(open_symbols) >= cfg.max_open_positions:
        return f"max open positions ({cfg.max_open_positions})"
    return None
