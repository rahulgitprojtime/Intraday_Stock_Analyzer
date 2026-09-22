from src.utils.config import load_settings, load_strategy, load_universe


def test_settings_loads_with_safe_defaults():
    settings = load_settings()
    assert settings["mode"] == "DATA_ONLY"
    assert settings["live_trading_confirmed"] is False


def test_strategy_weights_sum_to_100():
    strategy = load_strategy()
    weights = strategy["scoring"]["weights"]
    assert sum(weights.values()) == 100


def test_universe_loads():
    universe = load_universe()
    assert "filters" in universe
    assert universe["filters"]["min_price"] > 0
