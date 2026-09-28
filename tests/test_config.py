import pytest

from src.utils.config import (
    load_settings,
    load_strategy,
    load_universe,
    validate_engine_weights,
)


def test_settings_has_no_trading_modes():
    settings = load_settings()
    assert "mode" not in settings and "risk" not in settings
    assert "live_trading_confirmed" not in settings


def test_engine_weights_are_the_eight_groups_and_sum_to_one():
    w = load_strategy()["engine"]["weights"]
    assert w == {"setup": 0.20, "volume": 0.20, "movement": 0.15, "momentum": 0.15,
                 "sector": 0.10, "market": 0.10, "liquidity": 0.05, "news": 0.05}
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_no_prebaked_future_weights():
    s = load_strategy()
    assert "scoring" not in s
    assert "weights" not in s["recommendation"]


@pytest.mark.parametrize(
    "weights", [{}, {"setup": 0.5, "in_play": 0.4}, {"setup": 1.2, "in_play": -0.2}]
)
def test_validate_engine_weights_rejects_bad(weights):
    with pytest.raises(ValueError):
        validate_engine_weights(weights)


def test_candle_staleness_setting():
    assert load_settings()["candles"]["stale_after_seconds"] == 120


def test_universe_loads():
    universe = load_universe()
    assert "filters" in universe
    assert universe["filters"]["min_price"] > 0
