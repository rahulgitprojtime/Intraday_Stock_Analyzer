from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.context import MarketContext, nifty_context
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.in_play import InPlayConfig
from src.quantitative.liquidity import LiquidityHistory
from src.recommendation.engine import (
    SymbolInputs,
    avoid_reasons,
    evaluate_symbol,
    rank_recommendations,
)
from src.recommendation.models import MODES
from src.quantitative.setups import SetupSignal, SetupState
from src.recommendation.scoring import EngineConfig, blend
from src.utils.config import load_strategy

STRATEGY = load_strategy()
CFG = EngineConfig.from_strategy(STRATEGY)
IP_CFG = InPlayConfig.from_dict(STRATEGY["in_play"])
FILTERS = {"min_price": 20.0, "min_avg_daily_volume": 500_000, "min_avg_traded_value": 5e7}
T0 = datetime(2026, 9, 25, 9, 15)
AS_OF = T0 + timedelta(minutes=45)                      # 10:00, bars 09:15..09:59 closed
CURVE = [100.0 * (i + 1) for i in range(375)]
LIQ = LiquidityHistory(1e6, 1e9, 20)
PREP = DailyPrep(prev_high=101, prev_low=99, prev_close=100, pivot=100, cpr_top=100.1,
                 cpr_bottom=99.9, cpr_width_pct=0.2, is_nr7=None, is_inside_day=None,
                 atr=2.0, atr_pct=2.0)
BANNED_KEYS = ("price", "target", "stop", "entry", "level")
BANNED_PHRASES = ("chance of profit", "guaranteed", "probability of winning",
                  "expected return", "% chance")


def inst(sym, index=False):
    return Instrument(sym, Exchange.NSE, Segment.CASH, is_index=index)


def trend_bars(sym="HOT", n=45, step=0.05, vol=400):
    out, p = [], 100.0
    for i in range(n):
        o, c = p, p + step
        out.append(Candle(inst(sym), 1, T0 + timedelta(minutes=i), o, c + 0.02, o - 0.02, c, vol))
        p = c
    return out


INDEX = [Candle(inst("NIFTY", True), 1, T0 + timedelta(minutes=i), 1000, 1000, 1000, 1000, 0)
         for i in range(45)]
MARKET = nifty_context(INDEX, AS_OF, 120, CFG.market_ramp_pct)


def evaluate(bars=None, *, sym="HOT", prep=PREP, liq=LIQ, market=MARKET, as_of=AS_OF,
             curve=CURVE, cfg=CFG):
    inp = SymbolInputs(sym, bars if bars is not None else trend_bars(sym), prep, curve, liq, INDEX)
    return evaluate_symbol(inp, market, as_of, cfg, IP_CFG, FILTERS, 120)


def walk(obj, key=None):
    """Yield (key, value) pairs for every leaf in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, k)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk(v, key)
    else:
        yield key, obj


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from all_keys(v)


def test_hot_stock_scored_in_both_modes():
    ev = evaluate()
    assert ev.excluded_reason is None and ev.is_in_play
    assert set(ev.recommendations) == set(MODES)
    for rec in ev.recommendations.values():
        assert 0 <= rec.score <= 100
        assert rec.category in ("STRONG_CANDIDATE", "CANDIDATE", "WATCH", "NEUTRAL", "AVOID")
        assert rec.profile.startswith("IN_PLAY")
        assert rec.data_quality.status == "OK"


def test_future_components_are_unavailable_not_fabricated():
    rec = evaluate().recommendations["DAY"]
    assert rec.sector_context["status"] == "unavailable"
    assert all(v is None for k, v in rec.sector_context.items() if k != "status")
    assert rec.qualitative == {"status": "unavailable"}
    q = rec.quantitative
    assert q["momentum_score"] is None and q["trend_score"] is None
    assert q["liquidity"]["spread_pct"] is None
    comps = {c.name: c for c in rec.components}
    assert comps["sector_context"].value is None and comps["qualitative"].value is None


def test_score_is_blend_of_available_components_plus_adjustments():
    rec = evaluate().recommendations["SCALP"]
    assert rec.score == pytest.approx(blend(rec.components) + sum(a.points for a in rec.adjustments))


def test_deterministic():
    assert evaluate().recommendations["DAY"].to_dict() == evaluate().recommendations["DAY"].to_dict()


def test_stale_candles_excluded():
    ev = evaluate(as_of=AS_OF + timedelta(minutes=10))
    assert ev.recommendations == {} and "stale" in ev.excluded_reason.lower()


def test_missing_prep_excluded():
    ev = evaluate(prep=None)
    assert ev.recommendations == {} and "incomplete" in ev.excluded_reason.lower()


def test_illiquid_excluded():
    ev = evaluate(liq=LiquidityHistory(1e4, 1e6, 20))
    assert ev.recommendations == {} and ev.excluded_reason.startswith("liquidity")


def test_not_in_play_forced_avoid_with_reason():
    rec = evaluate(trend_bars(vol=50)).recommendations["DAY"]
    assert rec.category == "AVOID" and rec.profile.startswith("NOT_IN_PLAY")
    assert rec.eligible_for_top_n is False and "not in play" in rec.exclusion_reasons
    assert any(r.kind == "penalty" and "Not in play" in r.text for r in rec.reasons)


def test_market_unavailable_excluded_from_blend():
    rec = evaluate(market=MarketContext("unavailable", None, None)).recommendations["SCALP"]
    comps = {c.name: c for c in rec.components}
    assert comps["market_context"].status == "unavailable"
    assert rec.market_context["score"] is None


def test_zero_volume_stock_does_not_crash():
    rec = evaluate(trend_bars(vol=0)).recommendations["SCALP"]
    assert rec.category == "AVOID"


def test_forming_bar_ignored():
    bars = trend_bars()
    forming = Candle(inst("HOT"), 1, AS_OF, 102, 150, 102, 150, 99999, is_complete=False)
    assert evaluate(bars + [forming]).recommendations["DAY"].to_dict() == \
        evaluate(bars).recommendations["DAY"].to_dict()


def test_no_price_fields_and_no_banned_phrases():
    d = evaluate().recommendations["DAY"].to_dict()
    assert not [k for k in all_keys(d) if any(b in k.lower() for b in BANNED_KEYS)]
    text = " ".join(str(v) for _, v in walk(d) if isinstance(v, str)).lower()
    assert not [p for p in BANNED_PHRASES if p in text]


def test_reasons_are_traceable_to_computed_values():
    rec = evaluate().recommendations["DAY"]
    leaves = list(walk({k: v for k, v in rec.to_dict().items() if k != "reasons"}))
    assert rec.reasons
    for reason in rec.reasons:
        assert reason.kind and reason.text and reason.evidence
        for pair in reason.evidence.items():
            assert pair in leaves, (reason, pair)


def test_rank_orders_eligible_by_score_then_symbol_avoid_unranked():
    recs = [evaluate(sym=s).recommendations["DAY"] for s in ("CCC", "AAA", "BBB")]
    low = evaluate(trend_bars("ZZZ", vol=50), sym="ZZZ").recommendations["DAY"]
    ranked = rank_recommendations([low] + recs)
    assert [r.symbol for r in ranked] == ["AAA", "BBB", "CCC", "ZZZ"]
    assert [r.rank for r in ranked] == [1, 2, 3, None]      # AVOID kept, never ranked
    assert all(r.previous_rank is None and r.rank_change is None for r in ranked)


def test_lower_non_avoid_outranks_higher_avoid():
    base = evaluate().recommendations["DAY"]
    avoid = replace(base, symbol="HIGH", score=95.0, category="AVOID", eligible_for_top_n=False,
                    exclusion_reasons=("not in play",))
    cand = replace(base, symbol="LOW", score=85.0, category="STRONG_CANDIDATE")
    ranked = rank_recommendations([avoid, cand])
    assert [(r.symbol, r.rank) for r in ranked] == [("LOW", 1), ("HIGH", None)]


def test_avoid_reasons_are_the_hard_quality_gates():
    trig = SetupSignal("ORB5", SetupState.TRIGGERED, "")
    failed = SetupSignal("ORB5", SetupState.FAILED, "")
    assert avoid_reasons(True, trig, "CANDIDATE") == ()
    assert avoid_reasons(False, trig, "CANDIDATE") == ("not in play",)
    assert avoid_reasons(True, failed, "WATCH") == ("best setup failed",)
    assert avoid_reasons(True, trig, "AVOID") == ("score below NEUTRAL floor",)


def test_soft_penalty_stays_rankable():
    lunch = T0 + timedelta(minutes=165)                    # 12:00
    rec = evaluate(trend_bars(n=165), as_of=lunch).recommendations["SCALP"]
    assert any(a.name == "lunch_lull" and a.kind == "penalty" for a in rec.adjustments)
    assert rec.eligible_for_top_n is True and rec.category != "AVOID"


def breakout_bars():
    """Flat opening range, then one breakout bar: several setup families fire."""
    bars = [Candle(inst("BRK"), 1, T0 + timedelta(minutes=i), 100, 100.2, 99.9, 100, 400)
            for i in range(5)]
    bars += [Candle(inst("BRK"), 1, T0 + timedelta(minutes=i), 100, 100.1, 99.95, 100, 400)
             for i in range(5, 44)]
    bars.append(Candle(inst("BRK"), 1, T0 + timedelta(minutes=44), 100, 100.55, 100, 100.5, 400))
    return bars


def test_confluence_changes_final_score():
    rec = evaluate(breakout_bars(), sym="BRK").recommendations["SCALP"]
    assert len(rec.setup["confluence_families"]) >= 2
    bonus = next(a for a in rec.adjustments if a.name == "confluence")
    assert bonus.kind == "bonus" and 0 < bonus.points <= 5
    plain = evaluate(breakout_bars(), sym="BRK",
                     cfg=replace(CFG, confluence_bonus={})).recommendations["SCALP"]
    assert rec.score == pytest.approx(plain.score + bonus.points)
    assert any(r.kind == "setup" and "families" in r.text for r in rec.reasons)
