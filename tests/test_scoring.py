from datetime import time

import pytest

from src.quantitative.setups import SetupSignal, SetupState as S
from src.recommendation.models import AVAILABLE, UNAVAILABLE, Component
from src.recommendation.scoring import (
    EngineConfig,
    apply_time_rules,
    blend,
    categorize,
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


def test_best_is_highest_state_and_confluence_capped():
    signals = [sig("A", S.FORMING)] + [sig(n, S.TRIGGERED) for n in "BCD"]
    score, best, k = setup_score(signals, CFG)
    assert best.name == "B" and score == 100
    assert k == 2                         # 2 extra TRIGGERED, capped at confluence_max_extra


def test_no_setup_and_failed_best():
    assert setup_score([sig("A", S.NONE)], CFG) == (0.0, None, 0)
    assert setup_score([], CFG) == (0.0, None, 0)
    _, best, _ = setup_score([sig("A", S.NONE), sig("B", S.FAILED)], CFG)
    assert best.name == "B"


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
