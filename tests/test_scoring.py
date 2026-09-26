from datetime import time

import pytest

from src.quantitative.setups import SetupSignal, SetupState as S
from src.recommendation.models import AVAILABLE, UNAVAILABLE, Component
from src.recommendation.scoring import (
    EngineConfig,
    apply_time_rules,
    blend,
    categorize,
    confluence,
    setup_score,
)
from src.utils.config import load_strategy

CFG = EngineConfig.from_strategy(load_strategy())


def sig(name, state):
    return SetupSignal(name, state, "detail")


# --- setup score --------------------------------------------------------------
def test_state_points():
    assert setup_score([sig("A", S.TRIGGERED)], CFG)[0] == 100
    assert setup_score([sig("A", S.FORMING)], CFG)[0] == 60
    assert setup_score([sig("A", S.EXTENDED)], CFG)[0] == 30


def test_best_is_highest_state():
    signals = [sig("A", S.FORMING)] + [sig(n, S.TRIGGERED) for n in "BCD"]
    score, best = setup_score(signals, CFG)
    assert best.name == "B" and score == 100


def test_no_setup_and_failed_best():
    assert setup_score([sig("A", S.NONE)], CFG) == (0.0, None)
    assert setup_score([], CFG) == (0.0, None)
    _, best = setup_score([sig("A", S.NONE), sig("B", S.FAILED)], CFG)
    assert best.name == "B"


# --- confluence (independent setup families) -----------------------------------
@pytest.mark.parametrize("names,bonus", [
    ([], 0),
    (["ORB5"], 0),                                           # one family is not confluence
    (["ORB5", "VWAP_RECLAIM"], 2),
    (["ORB5", "VWAP_RECLAIM", "EMA_PULLBACK"], 3),
    (["ORB5", "VWAP_RECLAIM", "EMA_PULLBACK", "MOMENTUM_BURST"], 5),
    (["ORB5", "VWAP_RECLAIM", "EMA_PULLBACK", "MOMENTUM_BURST", "RS_VS_NIFTY"], 5),  # cap
])
def test_confluence_bonus_table_and_cap(names, bonus):
    families, got = confluence([sig(n, S.TRIGGERED) for n in names], CFG)
    assert got == bonus and len(families) == len(names)


def test_correlated_setups_in_one_family_count_once():
    same_family = ["ORB15", "PDH", "GAP_AND_GO", "NARROW_CPR"]      # all price structure
    families, bonus = confluence([sig(n, S.TRIGGERED) for n in same_family], CFG)
    assert families == ("price_structure",) and bonus == 0
    vwap_twice = [sig("VWAP_RECLAIM", S.TRIGGERED), sig("VWAP_PULLBACK", S.FORMING),
                  sig("ORB15", S.TRIGGERED)]
    assert confluence(vwap_twice, CFG) == (("price_structure", "vwap"), 2)


def test_only_active_states_count_toward_confluence():
    signals = [sig("ORB5", S.EXTENDED), sig("VWAP_RECLAIM", S.FAILED), sig("EMA_PULLBACK", S.NONE),
               sig("MOMENTUM_BURST", S.FORMING)]
    assert confluence(signals, CFG) == (("momentum",), 0)


# --- blend ----------------------------------------------------------------------
BASE = [Component("setup", 100, 0.55, AVAILABLE), Component("in_play", 80, 0.35, AVAILABLE),
        Component("market_context", 70, 0.10, AVAILABLE)]


def test_blend_baseline():
    assert blend(BASE) == pytest.approx(90)


def test_unavailable_market_is_excluded_and_renormalized():
    comps = BASE[:2] + [Component("market_context", None, 0.10, UNAVAILABLE)]
    assert blend(comps) == pytest.approx((55 + 28) / 0.9)


def test_unavailable_or_unweighted_components_never_change_score():
    extra = [Component("sector_context", None, 0.2, UNAVAILABLE),
             Component("qualitative", None, None, UNAVAILABLE),
             Component("liquidity", 90, None, AVAILABLE)]
    assert blend(BASE + extra) == blend(BASE)


def test_blend_nothing_available():
    assert blend([Component("x", None, 1.0, UNAVAILABLE)]) is None


# --- time-of-day heuristics ----------------------------------------------------
def test_midmorning_unchanged():
    assert apply_time_rules(90, "DAY", time(10, 0), CFG) == (90, [])


def test_lunch_penalty_and_clip():
    score, adj = apply_time_rules(90, "SCALP", time(12, 0), CFG)
    assert score == 80 and adj[0].kind == "penalty" and adj[0].points == -10
    assert apply_time_rules(5, "SCALP", time(12, 0), CFG)[0] == 0


def test_caps():
    assert apply_time_rules(90, "SCALP", time(9, 17), CFG)[0] == pytest.approx(64.99)
    assert apply_time_rules(90, "DAY", time(14, 50), CFG)[0] == pytest.approx(64.99)
    assert apply_time_rules(90, "SCALP", time(14, 50), CFG)[0] == 90
    score, adj = apply_time_rules(90, "SCALP", time(15, 5), CFG)
    assert score == pytest.approx(49.99) and adj[-1].kind == "cap"
    assert apply_time_rules(40, "SCALP", time(15, 5), CFG) == (40, [])   # below cap: no-op


def test_heuristic_reasons_say_unvalidated():
    _, adj = apply_time_rules(90, "DAY", time(12, 0), CFG)
    assert "heuristic" in adj[0].reason


# --- categories -------------------------------------------------------------------
@pytest.mark.parametrize("score,cat", [(80, "STRONG_CANDIDATE"), (79.99, "CANDIDATE"),
                                       (65, "CANDIDATE"), (64.99, "WATCH"), (50, "WATCH"),
                                       (49.99, "NEUTRAL"), (35, "NEUTRAL"), (34.99, "AVOID")])
def test_category_boundaries(score, cat):
    assert categorize(score, CFG) == cat
