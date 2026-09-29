import random

import pytest

from src.research.experiments import paired_ic, reblend


def test_reblend_uses_available_groups_only():
    row = {"g_setup": 100.0, "g_volume": 0.0, "g_news": None, "g_sector": None}
    w = {"setup": 0.5, "volume": 0.1, "sector": 0.1, "news": 0.3}
    assert reblend(row, w) == pytest.approx(100 * 0.5 / 0.6)
    assert reblend({"g_setup": None}, w) is None


def test_paired_ic_detects_the_better_ranking():
    rng = random.Random(3)
    rows = []
    for d in range(30):
        for i in range(20):
            signal = rng.gauss(0, 1)
            rows.append({"day": f"d{d:02d}", "good": signal, "bad": rng.gauss(0, 1),
                         "xs_nifty_15": signal * 0.1 + rng.gauss(0, 0.05)})
    diff, t, n, ic_a, ic_b = paired_ic(rows, lambda r: r["good"], lambda r: r["bad"], "xs_nifty_15")
    assert n == 30 and diff > 0.5 and t > 2 and ic_a > ic_b
    diff, t, *_ = paired_ic(rows, lambda r: r["bad"], lambda r: r["bad"], "xs_nifty_15")
    assert diff == 0 and t is None
