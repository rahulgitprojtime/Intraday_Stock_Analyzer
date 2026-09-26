"""Recommendation engine — M6 (spec §3-§18).

Pure and deterministic: same inputs → same output. Uses only the candles
it is given (sources guarantee none after `as_of`) and drops forming bars.
Pipeline per symbol: data quality → liquidity gate → in-play → setups per
mode → component blend → time heuristics → category → reasons.
Recommends only; nothing here places orders or emits price levels.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from src.data.candles import resample
from src.data.models import Candle
from src.market.context import MarketContext
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.in_play import InPlayConfig, InPlayResult, score_in_play
from src.quantitative.liquidity import Liquidity, LiquidityHistory, evaluate_liquidity
from src.quantitative.setups import (
    SetupSignal,
    SetupState,
    ema_pullback,
    gap_and_go,
    momentum_burst,
    narrow_cpr_trend,
    opening_range_breakout,
    pdh_breakout,
    relative_strength,
    vwap_pullback,
    vwap_reclaim,
)
from src.recommendation.data_quality import assess_data_quality
from src.recommendation.models import (
    AVAILABLE,
    MODES,
    UNAVAILABLE,
    Adjustment,
    Component,
    Reason,
    Recommendation,
    qualitative_unavailable,
    sector_unavailable,
)
from src.recommendation.scoring import (
    EngineConfig,
    apply_time_rules,
    blend,
    categorize,
    confluence,
    setup_score,
)

ACTIVE = (SetupState.TRIGGERED, SetupState.FORMING)
MOMENTUM_SETUPS = {"MOMENTUM_BURST", "RS_VS_NIFTY"}


@dataclass(frozen=True)
class SymbolInputs:
    symbol: str
    minute_bars: Sequence[Candle]       # today's 1-min bars up to as_of
    prep: DailyPrep | None
    volume_curve: Sequence[float]
    liquidity: LiquidityHistory | None
    index_bars: Sequence[Candle]        # NIFTY 1-min bars up to as_of


@dataclass(frozen=True)
class SymbolEvaluation:
    symbol: str
    recommendations: dict = field(default_factory=dict)   # mode -> Recommendation
    excluded_reason: str | None = None
    is_in_play: bool = False


def extension(mode: str, prep: DailyPrep, cfg: EngineConfig) -> float:
    if prep.atr:
        return cfg.ext_atr_mult[mode] * prep.atr
    return prep.prev_close * cfg.ext_fallback_pct / 100


def run_setups(mode, bars, prep, index_bars, ext, as_of) -> list[SetupSignal]:
    if mode == "SCALP":
        return [opening_range_breakout(bars, 5, ext), vwap_reclaim(bars, ext),
                vwap_pullback(bars, ext), ema_pullback(bars, ext), momentum_burst(bars),
                relative_strength(bars, index_bars)]
    c = resample(bars, 5, now=as_of)
    return [opening_range_breakout(c, 15, ext), pdh_breakout(c, prep, ext), vwap_reclaim(c, ext),
            vwap_pullback(c, ext), ema_pullback(c, ext), narrow_cpr_trend(c, prep, ext),
            gap_and_go(c, prep, ext), relative_strength(c, index_bars)]


def profile(is_in_play: bool, best: SetupSignal | None) -> str:
    strong = best is not None and best.state in ACTIVE
    if is_in_play:
        return "IN_PLAY_STRONG_SETUP" if strong else "IN_PLAY_WEAK_SETUP"
    return "NOT_IN_PLAY_SETUP" if strong else "NOT_IN_PLAY"


def avoid_reasons(is_in_play: bool, best: SetupSignal | None, category: str) -> tuple[str, ...]:
    """Hard quality gates (DECISIONS #15): any reason → AVOID, not rankable.
    Everything else (time-of-day, extension, weak components) is a soft
    penalty on the score and stays rankable."""
    out = []
    if not is_in_play:
        out.append("not in play")
    if best is not None and best.state is SetupState.FAILED:
        out.append("best setup failed")
    if category == "AVOID":
        out.append("score below NEUTRAL floor")
    return tuple(out)


def _quantitative(sscore: float, ip: InPlayResult, liq: Liquidity) -> dict:
    c = ip.components
    volatility = None
    if ip.atr_pct is not None and ip.range_expansion is not None:
        volatility = (c["atr_pct"] + c["range_expansion"]) / 2
    return {
        "setup_score": sscore,
        "in_play_score": ip.score,
        "is_in_play": ip.is_in_play,
        "liquidity_score": liq.score,
        "momentum_score": None,              # evidence enters via setups in M6
        "trend_score": None,
        "volume_score": c["rvol"] if ip.rvol is not None else None,
        "volatility_score": volatility,
        "relative_strength_score": c["rs"] if ip.rs_pct is not None else None,
        "in_play_features": {"rvol": ip.rvol, "gap_pct": ip.gap_pct, "atr_pct": ip.atr_pct,
                             "range_expansion": ip.range_expansion, "rs_pct": ip.rs_pct},
        "liquidity": {"eligible": liq.eligible, "avg_daily_volume": liq.avg_daily_volume,
                      "avg_traded_value": liq.avg_traded_value,
                      "current_traded_value": liq.current_traded_value,
                      "spread_pct": liq.spread_pct},
    }


def _reasons(signals, ip, market, liq, adjustments, forced) -> tuple[Reason, ...]:
    out = []
    for s in signals:
        if s.state in ACTIVE:
            kind = "momentum" if s.name in MOMENTUM_SETUPS else "setup"
            out.append(Reason(kind, f"{s.name} {s.state.value.lower()}: {s.detail}",
                              {"name": s.name, "state": s.state.value}))
    if ip.rvol is not None:
        out.append(Reason("volume", f"Volume {ip.rvol:.1f}x the 20-session average for this "
                          "time of day", {"rvol": ip.rvol}))
    if market.status == AVAILABLE:
        out.append(Reason("market_context", f"NIFTY {market.nifty_change_pct:+.2f}% since open",
                          {"nifty_change_pct": market.nifty_change_pct}))
    if liq.avg_traded_value is not None:
        out.append(Reason("liquidity", f"Average traded value {liq.avg_traded_value / 1e7:.1f} "
                          f"Cr/day; {liq.reason}", {"avg_traded_value": liq.avg_traded_value}))
    out += [Reason("setup" if a.kind == "bonus" else "penalty", a.reason,
                   {"name": a.name, "points": a.points}) for a in adjustments]
    return tuple(out + forced)


def evaluate_symbol(
    inp: SymbolInputs,
    market: MarketContext,
    as_of: datetime,
    cfg: EngineConfig,
    in_play_cfg: InPlayConfig,
    liquidity_filters: dict,
    stale_after_seconds: float,
) -> SymbolEvaluation:
    bars = [c for c in inp.minute_bars if c.is_complete]
    dq = assess_data_quality(bars, as_of, stale_after_seconds, has_prep=inp.prep is not None,
                             has_volume_curve=bool(inp.volume_curve),
                             has_index=market.status == AVAILABLE)
    if dq.status != "OK":
        missing = f" (missing: {', '.join(dq.missing_inputs)})" if dq.missing_inputs else ""
        return SymbolEvaluation(inp.symbol, excluded_reason=f"data {dq.status.lower()}{missing}")
    liq = evaluate_liquidity(inp.liquidity, bars, liquidity_filters)
    if liq.eligible is not True:
        return SymbolEvaluation(inp.symbol, excluded_reason=f"liquidity: {liq.reason}")

    ip = score_in_play(inp.symbol, bars, inp.prep, inp.volume_curve, inp.index_bars, in_play_cfg)
    market_block = {"status": market.status, "score": market.score, "source": market.source,
                    "nifty_change_pct": market.nifty_change_pct}
    recs = {}
    for mode in MODES:
        signals = run_setups(mode, bars, inp.prep, inp.index_bars,
                             extension(mode, inp.prep, cfg), as_of)
        sscore, best = setup_score(signals, cfg)
        families, bonus = confluence(signals, cfg)
        w = cfg.weights.get
        comps = (
            Component("setup", sscore, w("setup"), AVAILABLE),
            Component("in_play", ip.score, w("in_play"), AVAILABLE),
            Component("market_context", market.score, w("market_context"), market.status),
            Component("liquidity", liq.score, w("liquidity"),
                      AVAILABLE if liq.score is not None else UNAVAILABLE),
            Component("sector_context", None, w("sector_context"), UNAVAILABLE),
            Component("qualitative", None, w("qualitative"), UNAVAILABLE),
        )
        base = blend(comps)
        applied = min(bonus, 100.0 - base)       # effective bonus after the 100 ceiling
        pre = []
        if applied > 0:
            pre.append(Adjustment("confluence", "bonus", applied,
                                  f"{len(families)} independent setup families active: "
                                  f"{', '.join(families)}"))
        score, adjustments = apply_time_rules(base + applied, mode, as_of.time(), cfg)
        adjustments = pre + adjustments
        category = categorize(score, cfg)
        exclusions = avoid_reasons(ip.is_in_play, best, category)
        forced = []
        if "not in play" in exclusions:
            forced.append(Reason("penalty", f"Not in play ({ip.reason}): category set to AVOID",
                                 {"is_in_play": False}))
        if "best setup failed" in exclusions:
            forced.append(Reason("penalty", f"{best.name} failed: category set to AVOID",
                                 {"best_state": "FAILED"}))
        if "score below NEUTRAL floor" in exclusions:
            forced.append(Reason("penalty", "Score below the NEUTRAL floor: category AVOID",
                                 {"category": "AVOID"}))
        setup_block = {
            "best": best.name if best else None,
            "best_state": best.state.value if best else "NONE",
            "confluence_families": list(families),
            "confluence_count": len(families),
            "confluence_bonus": applied,
            "signals": [{"name": s.name, "state": s.state.value, "detail": s.detail}
                        for s in signals],
        }
        recs[mode] = Recommendation(
            symbol=inp.symbol, mode=mode, as_of=as_of.isoformat(), score=score,
            category="AVOID" if exclusions else category,
            profile=profile(ip.is_in_play, best), components=comps,
            quantitative=_quantitative(sscore, ip, liq), setup=setup_block,
            market_context=market_block, sector_context=sector_unavailable(),
            qualitative=qualitative_unavailable(), adjustments=tuple(adjustments),
            reasons=_reasons(signals, ip, market, liq, adjustments, forced), data_quality=dq,
            eligible_for_top_n=not exclusions, exclusion_reasons=exclusions,
        )
    return SymbolEvaluation(inp.symbol, recs, None, ip.is_in_play)


def rank_recommendations(recs: Sequence[Recommendation]) -> list[Recommendation]:
    """Exclude AVOID, rank the rest by score desc then symbol asc
    (deterministic ties). AVOID follows, unranked (rank None), kept for
    transparency. Rank history fields stay None until a later milestone."""
    key = lambda r: (-r.score, r.symbol)  # noqa: E731
    eligible = sorted((r for r in recs if r.eligible_for_top_n), key=key)
    avoid = sorted((r for r in recs if not r.eligible_for_top_n), key=key)
    return [replace(r, rank=i) for i, r in enumerate(eligible, 1)] + \
        [replace(r, rank=None) for r in avoid]
