from datetime import datetime, timedelta

import pytest

from src.data.feed_store import FeedStore
from src.data.models import DepthSnapshot, Tick
from src.quantitative.microstructure import (
    MicroConfig,
    imbalance,
    micro_score,
    spread_pct,
    symbol_feed,
    tick_velocity,
)
from src.utils.config import load_strategy

T = datetime(2026, 9, 28, 11, 0, 0)
CFG = MicroConfig()


def test_spread_pct_reference_and_invalid():
    assert spread_pct(99.95, 100.05) == pytest.approx(0.1)
    assert spread_pct(0, 100) is None
    assert spread_pct(100, 0) is None
    assert spread_pct(100.1, 100.0) is None          # crossed book


def test_imbalance_reference_and_empty():
    assert imbalance(300, 100) == pytest.approx(0.5)
    assert imbalance(100, 300) == pytest.approx(-0.5)
    assert imbalance(0, 0) is None


def test_tick_velocity_floor_on_quiet_average():
    assert tick_velocity(30, 20.0) == pytest.approx(1.5)
    assert tick_velocity(3, 0.2) == pytest.approx(3.0)   # avg floored at 1


def test_micro_score_ramps_and_renormalizes():
    assert micro_score(0.4, 2.5, CFG) == pytest.approx(100)
    assert micro_score(-0.3, 0.5, CFG) == 0
    assert micro_score(0.2, 1.75, CFG) == pytest.approx(50)
    assert micro_score(0.2, None, CFG) == pytest.approx(50)       # imbalance only
    assert micro_score(None, 2.5, CFG) == pytest.approx(100)      # velocity only
    assert micro_score(None, None, CFG) is None


def test_config_from_strategy_yaml():
    cfg = MicroConfig.from_dict(load_strategy()["microstructure"])
    assert cfg == MicroConfig()


def store_with(depth_age_s=0, tick_age_s=0, warm=True):
    s = FeedStore()
    if warm:
        s.count_tick("AAA", T - timedelta(seconds=tick_age_s + 300))   # observed 5+ min
    for i in range(60):
        s.count_tick("AAA", T - timedelta(seconds=tick_age_s + i))
    s.put_ltp(Tick("AAA", T, 100.0), T)
    s.put_depth(DepthSnapshot("AAA", T, 99.95, 10, 100.05, 10, 600, 200),
                T - timedelta(seconds=depth_age_s))
    return s


def test_symbol_feed_fresh():
    f = symbol_feed(store_with().snapshot(T), "AAA", 120, CFG)
    assert f.spread_pct == pytest.approx(0.1) and f.imbalance == pytest.approx(0.5)
    assert f.tick_velocity == pytest.approx(60 / 12.2)           # 60 in 1 min, 61 in 5
    assert f.micro_score == pytest.approx(100)
    assert f.last_tick_age_s == 0 and f.depth_age_s == 0


def test_symbol_feed_stale_depth_keeps_velocity_only():
    f = symbol_feed(store_with(depth_age_s=200).snapshot(T), "AAA", 120, CFG)
    assert f.spread_pct is None and f.imbalance is None
    assert f.tick_velocity is not None and f.micro_score is not None


def test_symbol_feed_none_when_ticks_stale_or_unknown():
    assert symbol_feed(store_with(tick_age_s=200).snapshot(T), "AAA", 120, CFG) is None
    assert symbol_feed(FeedStore().snapshot(T), "AAA", 120, CFG) is None


def test_velocity_unavailable_during_warm_up():
    """Live 2026-09-28: with < 5 min observed, the 5-min average is
    understated and velocity read 5.0 for every symbol."""
    f = symbol_feed(store_with(warm=False).snapshot(T), "AAA", 120, CFG)
    assert f.tick_velocity is None
    assert f.micro_score == pytest.approx(100)          # imbalance only, renormalized
