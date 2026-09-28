"""Recommendation data model — M6 (spec §8, DECISIONS #14).

`None` / status "unavailable" means not computed — never fabricated.
Blocks for future components (sector M8, qualitative M9) exist now so the
state schema and dashboard need no redesign. No price fields anywhere
(DECISIONS #11).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

AVAILABLE, UNAVAILABLE = "available", "unavailable"
MODES = ("SCALP", "DAY")


@dataclass(frozen=True)
class Component:
    name: str
    value: float | None
    weight: float | None     # None = no configured weight → reported, not blended
    status: str


@dataclass(frozen=True)
class Adjustment:
    name: str
    kind: str                # "cap" | "penalty" | "bonus"
    points: float            # signed change applied to the score
    reason: str


@dataclass(frozen=True)
class Reason:
    kind: str                # setup|volume|trend|momentum|market_context|liquidity|penalty
    text: str
    evidence: dict           # computed values the text was built from (no prices)


@dataclass(frozen=True)
class DataQuality:
    status: str                          # OK | STALE | INCOMPLETE
    market_data_timestamp: str | None    # last complete 1-min bar
    data_age_seconds: float | None
    stale: bool
    missing_inputs: tuple[str, ...] = ()


def sector_unavailable(reason: str = "sector context unavailable") -> dict:
    from src.market.sector import unavailable_context
    return unavailable_context(None, None, reason)


def qualitative_unavailable() -> dict:
    return {"status": UNAVAILABLE}


@dataclass(frozen=True)
class Recommendation:
    symbol: str
    mode: str
    as_of: str
    score: float
    category: str
    profile: str
    components: tuple[Component, ...]
    quantitative: dict
    setup: dict
    market_context: dict
    sector_context: dict
    qualitative: dict
    adjustments: tuple[Adjustment, ...]
    reasons: tuple[Reason, ...]
    data_quality: DataQuality
    eligible_for_top_n: bool = True      # False ⇔ category AVOID (hard quality gate)
    exclusion_reasons: tuple[str, ...] = ()
    rank: int | None = None              # None for AVOID: never ranked
    previous_rank: int | None = None     # rank history: later milestone
    rank_change: int | None = None
    score_change: float | None = None
    time_in_top_n: int | None = None
    prerequisites: tuple[dict, ...] = ()    # M8 checklist: {check, status, detail}
    prerequisites_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
