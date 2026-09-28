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
from src.quantitative.groups import (
    liquidity_group,
    market_group,
    momentum_group,
    news_group,
    price_group,
    sector_group,
    setup_group,
    volume_group,
)
from src.quantitative.in_play import InPlayConfig, InPlayResult, score_in_play
from src.quantitative.liquidity import Liquidity, LiquidityHistory, evaluate_liquidity
from src.quantitative.microstructure import SymbolFeed
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
from src.recommendation.prerequisites import build_checklist
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
    cap_score,
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
    feed: SymbolFeed | None = None      # live feed metrics (M7); None in replay/stale
    sector: dict | None = None          # sector_context block (M8); None → unavailable
    news: dict | None = None            # news verdict (M9); None → not checked (replay)
    bank_market: MarketContext | None = None   # BANK NIFTY, only for bank/finance stocks (M12)


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


def avoid_reasons(best: SetupSignal | None, category: str) -> tuple[str, ...]:
    """Hard quality gates (DECISIONS #15): any reason → AVOID, not rankable.
    Everything else (not in play, time-of-day, extension, weak components)
    is a soft penalty or cap on the score and stays rankable (DECISIONS #16)."""
    out = []
    if best is not None and best.state is SetupState.FAILED:
        out.append("best setup failed")
    if category == "AVOID":
        out.append("score below NEUTRAL floor")
    return tuple(out)


def _micro_block(feed: SymbolFeed | None) -> dict | None:
    if feed is None:
        return None
    return {"spread_pct": feed.spread_pct, "imbalance": feed.imbalance,
            "tick_velocity": feed.tick_velocity, "micro_score": feed.micro_score,
            "last_tick_age_s": feed.last_tick_age_s, "depth_age_s": feed.depth_age_s}


def _quantitative(sscore: float, ip: InPlayResult, liq: Liquidity,
                  feed: SymbolFeed | None) -> dict:
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
        "microstructure": _micro_block(feed),
    }


def _feed_reasons(feed: SymbolFeed | None, mode: str) -> list[Reason]:
    """Spread in both modes (a liquidity fact); imbalance/velocity feed the
    SCALP score only. Never bid/ask prices (no price levels, #11)."""
    if feed is None:
        return []
    out = []
    if feed.spread_pct is not None:
        out.append(Reason("liquidity", f"Spread {feed.spread_pct:.2f}%",
                          {"spread_pct": feed.spread_pct}))
    if mode == "SCALP" and feed.imbalance is not None:
        side = "more bids" if feed.imbalance > 0 else "more offers" if feed.imbalance < 0             else "balanced"
        out.append(Reason("momentum", f"Bid/ask imbalance {feed.imbalance:+.2f} ({side})",
                          {"imbalance": feed.imbalance}))
    if mode == "SCALP" and feed.tick_velocity is not None:
        out.append(Reason("momentum", f"Tick velocity {feed.tick_velocity:.1f}x the 5-min "
                          "average", {"tick_velocity": feed.tick_velocity}))
    return out


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
    liq = evaluate_liquidity(inp.liquidity, bars, liquidity_filters,
                             inp.feed.spread_pct if inp.feed else None)
    if liq.eligible is not True:
        return SymbolEvaluation(inp.symbol, excluded_reason=f"liquidity: {liq.reason}")

    ip = score_in_play(inp.symbol, bars, inp.prep, inp.volume_curve, inp.index_bars, in_play_cfg)
    sector = inp.sector or sector_unavailable()
    market_block = {"status": market.status, "score": market.score, "source": market.source,
                    "nifty_change_pct": market.nifty_change_pct}
    recs = {}
    for mode in MODES:
        signals = run_setups(mode, bars, inp.prep, inp.index_bars,
                             extension(mode, inp.prep, cfg), as_of)
        sscore, best = setup_score(signals, cfg)
        families, bonus = confluence(signals, cfg)
        # M12 (DECISIONS #22): one transparent score = weighted average of the
        # eight groups that have data. Weak sector, negative news or low
        # volume simply lower their group; no special caps.
        mode_bars = bars if mode == "SCALP" else [c for c in resample(bars, 5, now=as_of)
                                                   if c.is_complete]
        groups = (
            price_group(bars, inp.prep, ip.range_expansion),
            volume_group(ip.rvol, bars),
            momentum_group(mode_bars, 1.0 if mode == "SCALP" else 2.0),
            setup_group(sscore, bonus),
            market_group(market, inp.index_bars, inp.bank_market),
            sector_group(sector),
            liquidity_group(liq, inp.feed, mode),
            news_group(inp.news),
        )
        w = cfg.weights.get
        comps = tuple(Component(g.name, g.value, w(g.name) if g.value is not None else None,
                                AVAILABLE if g.value is not None else UNAVAILABLE)
                      for g in groups)
        score, adjustments = apply_time_rules(blend(comps), mode, as_of.time(), cfg)
        forced = []
        category = categorize(score, cfg)
        exclusions = avoid_reasons(best, category)
        checklist, summary = build_checklist(best, ip, liq, sector, market, inp.news)
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
            "confluence_bonus": bonus,
            "signals": [{"name": s.name, "state": s.state.value, "detail": s.detail}
                        for s in signals],
        }
        recs[mode] = Recommendation(
            symbol=inp.symbol, mode=mode, as_of=as_of.isoformat(), score=score,
            category="AVOID" if exclusions else category,
            profile=profile(ip.is_in_play, best), components=comps,
            quantitative=_quantitative(sscore, ip, liq, inp.feed) | {
                "groups": {g.name: {"value": g.value, "parts": g.parts} for g in groups}},
            setup=setup_block,
            market_context=market_block, sector_context=sector,
            qualitative=inp.news or qualitative_unavailable(), adjustments=tuple(adjustments),
            reasons=_reasons(signals, ip, market, liq, adjustments,
                             _feed_reasons(inp.feed, mode) + forced), data_quality=dq,
            eligible_for_top_n=not exclusions, exclusion_reasons=exclusions,
            prerequisites=checklist, prerequisites_summary=summary,
        )
    return SymbolEvaluation(inp.symbol, recs, None, ip.is_in_play)


def rank_recommendations(recs: Sequence[Recommendation]) -> list[Recommendation]:
    """Exclude AVOID, rank the rest by score desc, then pre-cap score desc
    (ties at a cap keep their underlying order, e.g. sector strength —
    replay 2026-09-25), then symbol. AVOID follows, unranked (rank None),
    kept for transparency. Rank history fields stay None until later."""
    def key(r):
        pre_cap = r.score - sum(a.points for a in r.adjustments if a.kind == "cap")
        return (-r.score, -pre_cap, r.symbol)
    eligible = sorted((r for r in recs if r.eligible_for_top_n), key=key)
    avoid = sorted((r for r in recs if not r.eligible_for_top_n), key=key)
    return [replace(r, rank=i) for i, r in enumerate(eligible, 1)] + \
        [replace(r, rank=None) for r in avoid]
