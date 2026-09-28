"""Sector confirmation — M8 (DECISIONS #18).

Is the stock's sector moving with it? Per sector: index % change since
open vs NIFTY's (`rs`) and how many *other* universe members are up
since open (a stock never confirms its own sector). Verdict CONFIRMED /
NEUTRAL / WEAK / UNAVAILABLE. Pure functions over closed 1-min candles;
missing or stale data → UNAVAILABLE, never guessed. Illustrative
thresholds (strategy.yaml `sector:`), unvalidated (M10).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.data.models import Candle
from src.utils.config import load_yaml

CONFIRMED, NEUTRAL, WEAK, UNAVAILABLE = "CONFIRMED", "NEUTRAL", "WEAK", "UNAVAILABLE"
EPS = 1e-9                     # threshold comparisons are inclusive despite float noise


@dataclass(frozen=True)
class SectorConfig:
    confirm_rs: float = 0.2
    weak_rs: float = -0.2
    confirm_peers: float = 0.6
    weak_peers: float = 0.4
    min_peers: int = 2
    rs_ramp: tuple = (-0.5, 0.5)
    weights: dict = field(default_factory=lambda: {"rs": 0.6, "peers": 0.4})

    @classmethod
    def from_dict(cls, d: dict) -> SectorConfig:
        base = cls()
        return cls(
            confirm_rs=float(d.get("confirm_rs", base.confirm_rs)),
            weak_rs=float(d.get("weak_rs", base.weak_rs)),
            confirm_peers=float(d.get("confirm_peers", base.confirm_peers)),
            weak_peers=float(d.get("weak_peers", base.weak_peers)),
            min_peers=int(d.get("min_peers", base.min_peers)),
            rs_ramp=tuple(d.get("rs_ramp", base.rs_ramp)),
            weights=dict(d.get("weights", base.weights)),
        )


@dataclass(frozen=True)
class SectorState:
    name: str
    index: str
    index_change_pct: float | None
    rs_vs_nifty: float | None
    members: tuple[str, ...]                # all configured members
    member_changes: Mapping[str, float]     # usable (fresh) members: symbol -> % since open


def load_sector_map(universe: Sequence[str], raw: dict | None = None
                    ) -> tuple[dict, list[str]]:
    """Validated `{sector: {index, members}}` + problems. Bad entries are
    skipped and reported, never guessed; a stock keeps its first sector."""
    if raw is None:
        raw = load_yaml("sectors.yaml").get("sectors") or {}
    uni, seen, out, problems = set(universe), set(), {}, []
    for name, entry in raw.items():
        index = (entry or {}).get("index")
        if not index:
            problems.append(f"sector {name}: no index")
            continue
        members = []
        for m in entry.get("members") or []:
            if m not in uni:
                problems.append(f"sector {name}: {m} not in universe")
            elif m in seen:
                problems.append(f"sector {name}: {m} already in another sector")
            else:
                seen.add(m)
                members.append(m)
        out[name] = {"index": index, "members": members}
    return out, problems


def _closed(bars: Sequence[Candle]) -> list[Candle]:
    return [c for c in bars if c.is_complete]


def change_since_open(bars: Sequence[Candle]) -> float | None:
    done = _closed(bars)
    if not done or not done[0].open:
        return None
    return (done[-1].close - done[0].open) / done[0].open * 100


def _fresh(bars: Sequence[Candle], as_of: datetime, stale_after_s: float) -> bool:
    done = _closed(bars)
    if not done:
        return False
    last = done[-1]
    end = last.timestamp + timedelta(minutes=last.timeframe_minutes)
    return (as_of - end).total_seconds() <= stale_after_s


def sector_snapshot(sectors: dict, index_bars: Mapping[str, Sequence[Candle]],
                    stock_bars: Mapping[str, Sequence[Candle]], nifty_change: float | None,
                    as_of: datetime, stale_after_s: float, cfg: SectorConfig
                    ) -> dict[str, SectorState]:
    out = {}
    for name, s in sectors.items():
        bars = index_bars.get(s["index"], [])
        chg = change_since_open(bars) if _fresh(bars, as_of, stale_after_s) else None
        rs = chg - nifty_change if chg is not None and nifty_change is not None else None
        members = {}
        for m in s["members"]:
            mb = stock_bars.get(m, [])
            c = change_since_open(mb) if _fresh(mb, as_of, stale_after_s) else None
            if c is not None:
                members[m] = c
        out[name] = SectorState(name, s["index"], chg, rs, tuple(s["members"]), members)
    return out


def _ramp(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _unavailable(sector: str | None, index: str | None, reason: str) -> dict:
    return {"status": "unavailable", "sector": sector, "index": index, "sector_score": None,
            "sector_relative_strength": None, "stock_vs_sector": None, "peers_up": None,
            "peers_total": None, "verdict": UNAVAILABLE, "sector_market_alignment": None,
            "reason": reason}


def stock_context(symbol: str, snapshot: Mapping[str, SectorState],
                  stock_bars: Sequence[Candle], cfg: SectorConfig) -> dict:
    """The `sector_context` block for one stock (no prices)."""
    st = next((s for s in snapshot.values() if symbol in s.members), None)
    if st is None:
        return _unavailable(None, None, "no sector index for this stock")
    if st.rs_vs_nifty is None:
        return _unavailable(st.name, st.index, "sector index or NIFTY data unavailable")
    peers = [c for m, c in st.member_changes.items() if m != symbol]
    use_peers = len(peers) >= cfg.min_peers
    up = sum(1 for c in peers if c > 0)
    share = up / len(peers) if use_peers else None
    rs = st.rs_vs_nifty
    if rs >= cfg.confirm_rs - EPS and (share is None or share >= cfg.confirm_peers - EPS):
        verdict = CONFIRMED
    elif rs <= cfg.weak_rs + EPS or (share is not None and share <= cfg.weak_peers + EPS):
        verdict = WEAK
    else:
        verdict = NEUTRAL
    parts = [(cfg.weights["rs"], _ramp(rs, *cfg.rs_ramp))]
    if share is not None:
        parts.append((cfg.weights["peers"], share * 100))
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    own = change_since_open(stock_bars)
    nifty = st.index_change_pct - rs
    peer_text = f", {up}/{len(peers)} peers up" if use_peers else ", index only"
    return {
        "status": "available", "sector": st.name, "index": st.index, "sector_score": score,
        "sector_relative_strength": rs,
        "stock_vs_sector": own - st.index_change_pct if own is not None else None,
        "peers_up": up if use_peers else 0, "peers_total": len(peers) if use_peers else 0,
        "verdict": verdict,
        "sector_market_alignment": (st.index_change_pct >= 0) == (nifty >= 0),
        "reason": f"{st.name} {rs:+.2f}% vs NIFTY{peer_text}",
    }
