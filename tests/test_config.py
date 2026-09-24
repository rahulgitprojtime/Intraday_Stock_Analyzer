from src.utils.config import load_settings, load_strategy, load_universe


def test_settings_has_no_trading_modes():
    settings = load_settings()
    assert "mode" not in settings and "risk" not in settings
    assert "live_trading_confirmed" not in settings


def test_recommendation_weights_sum_to_one():
    weights = load_strategy()["recommendation"]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_strategy_weights_sum_to_100():
    strategy = load_strategy()
    weights = strategy["scoring"]["weights"]
    assert sum(weights.values()) == 100


def test_universe_loads():
    universe = load_universe()
    assert "filters" in universe
    assert universe["filters"]["min_price"] > 0
