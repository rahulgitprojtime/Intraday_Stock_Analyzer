"""Microstructure from the live feed — M7 (spec 2026-09-28 §4).

Pure functions over a `FeedSnapshot`: spread %, bid/ask imbalance, tick
velocity and a 0-100 `micro_score` (SCALP only). Missing inputs are None,
never invented. Defaults are illustrative and unvalidated (M10).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data.feed_store import WINDOW, FeedSnapshot


@dataclass(frozen=True)
class MicroConfig:
    weights: dict = field(default_factory=lambda: {"imbalance": 0.6, "velocity": 0.4})
    ramps: dict = field(default_factory=lambda: {"imbalance": (0.0, 0.4),
                                                  "velocity": (1.0, 2.5)})

    @classmethod
    def from_dict(cls, d: dict) -> MicroConfig:
        base = cls()
        return cls(weights=dict(d.get("weights", base.weights)),
                   ramps={k: tuple(v) for k, v in d.get("ramps", base.ramps).items()})


@dataclass(frozen=True)
class SymbolFeed:
    spread_pct: float | None
    imbalance: float | None
    tick_velocity: float | None
    micro_score: float | None
    last_tick_age_s: float | None
    depth_age_s: float | None


def spread_pct(best_bid: float, best_ask: float) -> float | None:
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    return (best_ask - best_bid) / ((best_ask + best_bid) / 2) * 100


def imbalance(total_bid_qty: float, total_ask_qty: float) -> float | None:
    total = total_bid_qty + total_ask_qty
    return None if total <= 0 else (total_bid_qty - total_ask_qty) / total


def tick_velocity(ticks_1m: int, ticks_5m_avg: float) -> float:
    return ticks_1m / max(ticks_5m_avg, 1.0)


def _ramp(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100))


def micro_score(imb: float | None, vel: float | None, cfg: MicroConfig) -> float | None:
    """Weighted ramps over the inputs present; both missing → None."""
    parts = [(cfg.weights[k], _ramp(v, *cfg.ramps[k]))
             for k, v in (("imbalance", imb), ("velocity", vel)) if v is not None]
    total = sum(w for w, _ in parts)
    return sum(w * x for w, x in parts) / total if parts and total > 0 else None


def symbol_feed(snap: FeedSnapshot, symbol: str, stale_after_s: float,
                cfg: MicroConfig) -> SymbolFeed | None:
    """None when the symbol has no recent ticks; depth metrics None when
    depth is missing or older than `stale_after_s`; velocity None until
    5 min of ticks have been observed."""
    st = snap.symbols.get(symbol)
    if st is None or st.last_tick_age_s is None or st.last_tick_age_s > stale_after_s:
        return None
    sp = imb = None
    if st.depth is not None and st.depth_age_s is not None and st.depth_age_s <= stale_after_s:
        sp = spread_pct(st.depth.best_bid, st.depth.best_ask)
        imb = imbalance(st.depth.total_bid_qty, st.depth.total_ask_qty)
    warm = st.observed_s is not None and st.observed_s >= WINDOW.total_seconds()
    vel = tick_velocity(st.ticks_1m, st.ticks_5m_avg) if warm else None   # avg understated
    return SymbolFeed(sp, imb, vel, micro_score(imb, vel, cfg), st.last_tick_age_s,
                      st.depth_age_s)
