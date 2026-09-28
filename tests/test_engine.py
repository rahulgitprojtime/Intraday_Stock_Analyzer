from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.market.context import MarketContext, nifty_context
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.in_play import InPlayConfig
from src.quantitative.liquidity import LiquidityHistory
from src.quantitative.microstructure import SymbolFeed
from src.recommendation.engine import (
    SymbolInputs,
    avoid_reasons,
    evaluate_symbol,
    rank_recommendations,
)
from src.recommendation.models import MODES, Adjustment
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
             curve=CURVE, cfg=CFG, ip_cfg=IP_CFG, feed=None, filters=FILTERS, sector=None,
             news=None):
    inp = SymbolInputs(sym, bars if bars is not None else trend_bars(sym), prep, curve, liq, INDEX,
                       feed, sector, news)
    return evaluate_symbol(inp, market, as_of, cfg, ip_cfg, filters, 120)


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
    labels = ("status", "verdict", "reason")                   # labels, not values
    assert rec.sector_context["verdict"] == "UNAVAILABLE"
    assert all(v is None for k, v in rec.sector_context.items() if k not in labels)
    assert rec.qualitative == {"status": "unavailable"}
    q = rec.quantitative
    assert q["momentum_score"] is None and q["trend_score"] is None
    assert q["liquidity"]["spread_pct"] is None
    comps = {c.name: c for c in rec.components}
    assert comps["sector"].value is None and comps["news"].value is None


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


def test_low_volume_lowers_the_volume_group_without_a_cap():
    """M12: no special 'not in play' cap; weak volume just scores low."""
    quiet = evaluate(trend_bars(vol=50)).recommendations["DAY"]
    busy = evaluate().recommendations["DAY"]
    assert quiet.profile.startswith("NOT_IN_PLAY") and quiet.eligible_for_top_n
    assert comp(quiet, "volume").value < comp(busy, "volume").value
    assert quiet.score < busy.score
    assert not [a for a in quiet.adjustments if a.name == "not_in_play"]


def test_market_unavailable_excluded_from_blend():
    rec = evaluate(market=MarketContext("unavailable", None, None)).recommendations["SCALP"]
    parts = rec.quantitative["groups"]["market"]["parts"]
    assert parts["nifty"] is None                      # regime from index bars may remain
    assert rec.market_context["score"] is None


def test_zero_volume_stock_does_not_crash():
    rec = evaluate(trend_bars(vol=0)).recommendations["SCALP"]
    assert rec.category not in ("STRONG_CANDIDATE", "CANDIDATE")     # not in play


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
    low = replace(evaluate(sym="ZZZ").recommendations["DAY"], category="AVOID",
                  eligible_for_top_n=False, exclusion_reasons=("best setup failed",))
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
    assert avoid_reasons(trig, "CANDIDATE") == ()   # not-in-play is a WATCH cap, not a gate
    assert avoid_reasons(failed, "WATCH") == ("best setup failed",)
    assert avoid_reasons(trig, "AVOID") == ("score below NEUTRAL floor",)


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
    ip_all = replace(IP_CFG, min_score=0.0, min_rvol=0.0)    # keep the WATCH cap out of the way
    rec = evaluate(breakout_bars(), sym="BRK", ip_cfg=ip_all).recommendations["SCALP"]
    assert len(rec.setup["confluence_families"]) >= 2
    bonus = rec.setup["confluence_bonus"]
    assert 0 < bonus <= 5
    plain = evaluate(breakout_bars(), sym="BRK", cfg=replace(CFG, confluence_bonus={}),
                     ip_cfg=ip_all).recommendations["SCALP"]
    assert comp(rec, "setup").value == min(100, comp(plain, "setup").value + bonus)
    assert rec.score >= plain.score                    # confluence lives in the setup group


FEED = SymbolFeed(spread_pct=0.04, imbalance=0.32, tick_velocity=1.8, micro_score=80.0,
                  last_tick_age_s=1.0, depth_age_s=1.0)


def comp(rec, name):
    return next(c for c in rec.components if c.name == name)


def test_microstructure_component_is_scalp_only():
    ev = evaluate(feed=FEED)
    scalp, day = ev.recommendations["SCALP"], ev.recommendations["DAY"]
    micro = lambda r: r.quantitative["groups"]["liquidity"]["parts"]["microstructure"]  # noqa: E731
    assert micro(scalp) == 80.0 and micro(day) is None
    baseline = evaluate().recommendations["DAY"]
    assert comp(day, "liquidity").value is not None
    assert day.quantitative["groups"]["momentum"] == baseline.quantitative["groups"]["momentum"]


def test_no_feed_leaves_component_unavailable():
    scalp = evaluate().recommendations["SCALP"]
    assert scalp.quantitative["groups"]["liquidity"]["parts"]["microstructure"] is None
    assert scalp.quantitative["microstructure"] is None


def test_feed_changes_scalp_blend():
    ip_all = replace(IP_CFG, min_score=0.0, min_rvol=0.0)     # keep the WATCH cap away
    hi = evaluate(feed=replace(FEED, micro_score=100.0), ip_cfg=ip_all).recommendations["SCALP"]
    lo = evaluate(feed=replace(FEED, micro_score=0.0), ip_cfg=ip_all).recommendations["SCALP"]
    assert hi.score > lo.score


def test_feed_reasons_are_traceable_and_show_no_prices():
    rec = evaluate(feed=FEED).recommendations["SCALP"]
    texts = [r.text for r in rec.reasons]
    assert "Spread 0.04%" in texts
    assert "Bid/ask imbalance +0.32 (more bids)" in texts
    assert "Tick velocity 1.8x the 5-min average" in texts
    leaves = list(walk({k: v for k, v in rec.to_dict().items() if k != "reasons"}))
    for reason in rec.reasons:
        for pair in reason.evidence.items():
            assert pair in leaves, (reason, pair)
    d = rec.to_dict()
    assert not [k for k in all_keys(d) if any(b in k.lower() for b in BANNED_KEYS)]


def test_wide_spread_excludes_in_both_modes():
    ev = evaluate(feed=replace(FEED, spread_pct=0.9), filters=FILTERS | {"max_spread_pct": 0.5})
    assert ev.recommendations == {} and "spread above maximum" in ev.excluded_reason


def sector_block(verdict, score, rs):
    return {"status": "available", "sector": "IT", "index": "NIFTYIT", "sector_score": score,
            "sector_relative_strength": rs, "stock_vs_sector": 0.1, "peers_up": 3,
            "peers_total": 3 if verdict == "CONFIRMED" else 1, "verdict": verdict,
            "sector_market_alignment": True, "reason": f"IT {rs:+.2f}% vs NIFTY, x/3 peers up"}


IP_ALL = replace(IP_CFG, min_score=0.0, min_rvol=0.0)          # keep the in-play cap away


def test_confirmed_sector_outranks_neutral_and_is_reported():
    conf = evaluate(sector=sector_block("CONFIRMED", 90.0, 0.4), ip_cfg=IP_ALL)
    neut = evaluate(sector=sector_block("NEUTRAL", 30.0, 0.0), ip_cfg=IP_ALL)
    for mode in MODES:
        c, n = conf.recommendations[mode], neut.recommendations[mode]
        assert c.score > n.score
        assert comp(c, "sector").status == "available"
        assert c.sector_context["verdict"] == "CONFIRMED"


def test_weak_sector_lowers_the_sector_group_and_stays_rankable():
    """M12: a weak sector scores low in its group; no special cap or penalty."""
    weak = evaluate(sector=sector_block("WEAK", 10.0, -0.4), ip_cfg=IP_ALL).recommendations["DAY"]
    conf = evaluate(sector=sector_block("CONFIRMED", 90.0, 0.4),
                    ip_cfg=IP_ALL).recommendations["DAY"]
    assert comp(weak, "sector").value < comp(conf, "sector").value
    assert weak.score < conf.score and weak.eligible_for_top_n
    assert not [a for a in weak.adjustments if a.name.startswith("sector_weak")]


def test_weak_sector_ranks_below_capped_peers_on_a_quiet_day():
    """Replay 2026-09-25 10:00: every stock capped at WATCH for not being in
    play; a WEAK-sector stock must not sort above the others."""
    quiet = trend_bars(vol=50)                                  # not in play → capped
    weak = evaluate(quiet, sym="AAA", sector=sector_block("WEAK", 10.0, -0.4))
    conf = evaluate(quiet, sym="ZZZ", sector=sector_block("CONFIRMED", 90.0, 0.4))
    ranked = rank_recommendations([weak.recommendations["DAY"], conf.recommendations["DAY"]])
    assert [r.symbol for r in ranked] == ["ZZZ", "AAA"]


def test_ties_at_a_cap_break_by_pre_cap_score_not_symbol():
    base = evaluate().recommendations["DAY"]
    capped = lambda sym, pre: replace(base, symbol=sym, score=64.99, adjustments=(  # noqa: E731
        Adjustment("not_in_play", "cap", 64.99 - pre, "capped"),))
    ranked = rank_recommendations([capped("AAA", 70.0), capped("ZZZ", 90.0)])
    assert [r.symbol for r in ranked] == ["ZZZ", "AAA"]


def test_unavailable_sector_no_component_no_cap():
    base = evaluate(ip_cfg=IP_ALL).recommendations["DAY"]
    assert comp(base, "sector").status == "unavailable"
    assert base.sector_context["verdict"] == "UNAVAILABLE"
    assert not any("Sector weak" in r.text for r in base.reasons)


def checks(rec):
    return {c["check"]: c for c in rec.prerequisites}


def test_prerequisites_checklist_statuses_and_summary():
    rec = evaluate(sector=sector_block("CONFIRMED", 90.0, 0.4), feed=FEED,
                   ip_cfg=IP_ALL).recommendations["SCALP"]
    c = checks(rec)
    assert list(c) == ["technicals", "in_play", "liquidity", "sector", "market", "news"]
    assert c["technicals"]["status"] == "PASS" and c["in_play"]["status"] == "PASS"
    assert c["liquidity"]["status"] == "PASS" and "spread 0.04%" in c["liquidity"]["detail"]
    assert c["sector"]["status"] == "PASS" and "IT +0.40% vs NIFTY" in c["sector"]["detail"]
    assert c["news"] == {"check": "news", "status": "NOT_CHECKED",
                         "detail": "not checked (no live news source)"}
    assert rec.prerequisites_summary.startswith("Checked: technicals ✓")
    assert "news – not checked" in rec.prerequisites_summary


def test_prerequisites_fail_warn_and_na():
    rec = evaluate(trend_bars(vol=50), sector=sector_block("WEAK", 10.0, -0.4)
                   ).recommendations["DAY"]
    c = checks(rec)
    assert c["in_play"]["status"] == "FAIL" and c["sector"]["status"] == "FAIL"
    assert c["liquidity"]["status"] == "WARN"                          # spread unchecked
    none = evaluate(market=MarketContext("unavailable", None, None)).recommendations["DAY"]
    assert checks(none)["sector"]["status"] == "NA" and checks(none)["market"]["status"] == "NA"


def news_block(verdict, direction="UP", title="HOT bags Rs 900 crore order from NHAI"):
    items = [] if verdict in ("NO_RELEVANT_INFORMATION", "UNAVAILABLE") else [
        {"title": title, "source": "Mint", "outlets": 2, "published_at": "2026-09-25T09:40",
         "link": "https://news.example/1", "direction": direction, "phrase": "bags * order",
         "reason": "'bags * order'"}]
    return {"status": "unavailable" if verdict == "UNAVAILABLE" else "available",
            "verdict": verdict, "items": items, "reason": "news fetch failed",
            "lookback_hours": 18, "age_minutes": 3.0}


def test_positive_news_adds_credibility_bonus_and_passes_check():
    base = evaluate(ip_cfg=IP_ALL).recommendations["DAY"]
    rec = evaluate(ip_cfg=IP_ALL, news=news_block("POSITIVE")).recommendations["DAY"]
    assert comp(rec, "news").value == 100 and comp(base, "news").value is None
    assert rec.score >= base.score and rec.qualitative["verdict"] == "POSITIVE"
    n = checks(rec)["news"]
    assert n["status"] == "PASS"
    assert n["detail"] == "upward catalyst: 'HOT bags Rs 900 crore order from NHAI' (Mint +1 outlet, 09:40)"


def test_negative_news_scores_zero_in_its_group_without_a_cap():
    base = evaluate(ip_cfg=IP_ALL).recommendations["DAY"]
    rec = evaluate(ip_cfg=IP_ALL, news=news_block("NEGATIVE", "DOWN",
                                                  "Citi cuts HOT target")).recommendations["DAY"]
    assert comp(rec, "news").value == 0 and rec.score <= base.score and rec.eligible_for_top_n
    assert not [a for a in rec.adjustments if a.name.startswith("news_")]
    assert checks(rec)["news"]["status"] == "FAIL"
    assert "downward" in checks(rec)["news"]["detail"]


def test_news_mixed_none_and_unavailable_statuses():
    assert checks(evaluate(news=news_block("MIXED")).recommendations["DAY"])["news"]["status"]         == "WARN"
    none = checks(evaluate(news=news_block("NO_RELEVANT_INFORMATION")).recommendations["DAY"])
    assert none["news"] == {"check": "news", "status": "NA",
                            "detail": "no relevant news in the last 18h"}
    down = checks(evaluate(news=news_block("UNAVAILABLE")).recommendations["DAY"])
    assert down["news"]["status"] == "NA" and "news fetch failed" in down["news"]["detail"]


def test_news_links_are_kept_and_no_invented_text():
    rec = evaluate(news=news_block("POSITIVE")).recommendations["DAY"]
    assert rec.qualitative["items"][0]["link"] == "https://news.example/1"
    assert evaluate().recommendations["DAY"].qualitative == {"status": "unavailable"}


def test_news_pending_is_not_checked_with_reason():
    pend = {"status": "unavailable", "verdict": "PENDING", "items": [],
            "reason": "first news fetch pending"}
    n = checks(evaluate(news=pend).recommendations["DAY"])["news"]
    assert n == {"check": "news", "status": "NOT_CHECKED", "detail": "first news fetch pending"}
