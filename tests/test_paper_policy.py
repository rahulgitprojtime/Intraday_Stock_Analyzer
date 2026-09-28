from datetime import time

import pytest

from src.paper.policy import PaperConfig, eligibility
from src.paper.risk import initial_levels
from src.utils.config import load_yaml

CFG = PaperConfig.from_dict(load_yaml("paper.yaml"))


def rec(**kw):
    base = {"symbol": "AAA", "category": "CANDIDATE", "score": 70.0, "rank": 1,
            "eligible_for_top_n": True, "setup": {"best": "ORB15", "best_state": "TRIGGERED"},
            "quantitative": {"is_in_play": True}, "data_quality": {"status": "OK"}}
    return base | kw


def test_config_defaults_are_safe():
    assert CFG.enabled is False and CFG.mode == "DAY" and CFG.quantity == 10
    assert (CFG.stop_atr_mult, CFG.stop_fallback_pct, CFG.target_r) == (0.25, 0.6, 1.5)
    assert CFG.no_entry_after == time(15, 0) and CFG.eod_exit == time(15, 20)


def test_valid_candidate_qualifies():
    assert eligibility(rec(), CFG, open_symbols=set(), traded_today={}, at=time(10, 0)) is None


@pytest.mark.parametrize("change,reason", [
    ({"category": "WATCH"}, "category WATCH below CANDIDATE"),
    ({"score": 60.0}, "score 60.0 below 65"),
    ({"setup": {"best": "ORB15", "best_state": "FORMING"}}, "best setup not TRIGGERED"),
    ({"rank": 11}, "rank 11 outside top 10"),
    ({"eligible_for_top_n": False, "rank": None}, "not rankable (AVOID)"),
    ({"data_quality": {"status": "STALE"}}, "data STALE"),
])
def test_invalid_candidates_give_a_reason(change, reason):
    assert eligibility(rec(**change), CFG, set(), {}, time(10, 0)) == reason


def test_limits_and_time_cutoff():
    assert eligibility(rec(), CFG, {"AAA"}, {}, time(10, 0)) == "position already open"
    assert eligibility(rec(), CFG, set(), {"AAA": 1}, time(10, 0)) == "symbol traded today"
    assert eligibility(rec(), CFG, {"B", "C", "D"}, {}, time(10, 0)) == "max open positions (3)"
    assert eligibility(rec(), CFG, set(), {}, time(15, 1)) == "after no_entry_after 15:00"


def test_require_in_play_when_configured():
    strict = PaperConfig.from_dict(load_yaml("paper.yaml") | {
        "entry": load_yaml("paper.yaml")["entry"] | {"require_in_play": True}})
    assert eligibility(rec(quantitative={"is_in_play": False}), strict, set(), {},
                       time(10, 0)) == "not in play"


def test_stop_and_target_from_daily_atr_and_fallback():
    stop, target, method = initial_levels(1000.0, daily_atr=20.0, cfg=CFG)
    assert (stop, target, method) == (995.0, 1007.5, "atr")           # risk 5 = 0.25 x 20
    stop, target, method = initial_levels(1000.0, daily_atr=None, cfg=CFG)
    assert (stop, target, method) == (994.0, 1009.0, "pct_fallback")  # risk 6 = 0.6%
