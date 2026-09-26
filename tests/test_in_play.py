from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.in_play import InPlayConfig, rank_in_play, score_in_play
from src.utils.config import load_strategy

INST = Instrument("TEST", Exchange.NSE, Segment.CASH)
T0 = datetime(2026, 9, 25, 9, 15)
CURVE = [100.0 * (i + 1) for i in range(375)]   # 100 shares/min on average


def bar(i, o, h, l, c, v=100, complete=True, inst=INST):
    return Candle(inst, 1, T0 + timedelta(minutes=i), o, h, l, c, v, complete)


def prep(prev_close=100.0, atr=2.0):
    return DailyPrep(prev_high=101, prev_low=99, prev_close=prev_close, pivot=100,
                     cpr_top=100.1, cpr_bottom=99.9, cpr_width_pct=0.2, is_nr7=None,
                     is_inside_day=None, atr=atr, atr_pct=atr / prev_close * 100)


FLAT_IDX = [bar(0, 1000, 1000, 1000, 1000), bar(1, 1000, 1000, 1000, 1000)]


def test_features_from_candles():
    today = [bar(0, 102, 103, 101.5, 102.5, 300), bar(1, 102.5, 104, 102, 104, 300)]
    r = score_in_play("X", today, prep(), CURVE, FLAT_IDX)
    assert r.rvol == pytest.approx(600 / 200)
    assert r.gap_pct == pytest.approx(2.0)
    assert r.atr_pct == pytest.approx(2.0)
    assert r.range_expansion == pytest.approx((104 - 101.5) / 2.0)
    assert r.rs_pct == pytest.approx((104 / 102 - 1) * 100)


def test_hot_stock_is_in_play_and_quiet_is_not():
    hot = [bar(0, 103, 104, 102.8, 104, 400), bar(1, 104, 106, 104, 106, 400)]
    quiet = [bar(0, 100, 100.2, 99.9, 100.1, 50), bar(1, 100.1, 100.2, 100, 100.1, 50)]
    h = score_in_play("HOT", hot, prep(), CURVE, FLAT_IDX)
    q = score_in_play("QUIET", quiet, prep(), CURVE, FLAT_IDX)
    assert h.is_in_play and h.score > 80
    assert not q.is_in_play and q.score < 30


def test_low_rvol_gates_out_even_with_high_score():
    cfg = InPlayConfig(min_score=0, min_rvol=1.5)
    today = [bar(0, 103, 106, 103, 106, 100), bar(1, 106, 108, 106, 108, 100)]
    r = score_in_play("X", today, prep(), CURVE, FLAT_IDX, cfg)
    assert r.rvol == pytest.approx(1.0) and not r.is_in_play
    assert "RVOL" in r.reason


def test_gap_down_scores_zero_gap_component():
    today = [bar(0, 97, 97.5, 96.5, 97, 300), bar(1, 97, 97.5, 96.8, 97.2, 300)]
    r = score_in_play("X", today, prep(), CURVE, FLAT_IDX)
    assert r.gap_pct == pytest.approx(-3.0)
    assert r.components["gap"] == 0


def test_no_completed_bars_is_not_in_play():
    r = score_in_play("X", [bar(0, 100, 101, 99, 100, complete=False)], prep(), CURVE, FLAT_IDX)
    assert not r.is_in_play and r.score == 0


def test_rank_orders_by_score_and_keeps_only_in_play():
    hot = [bar(0, 103, 104, 102.8, 104, 400), bar(1, 104, 106, 104, 106, 400)]
    warm = [bar(0, 101, 102, 100.8, 102, 300), bar(1, 102, 103, 102, 103, 300)]
    quiet = [bar(0, 100, 100.2, 99.9, 100.1, 50)]
    results = [score_in_play(s, c, prep(), CURVE, FLAT_IDX)
               for s, c in [("QUIET", quiet), ("WARM", warm), ("HOT", hot)]]
    ranked = rank_in_play(results, top_n=5)
    assert [r.symbol for r in ranked] == ["HOT", "WARM"]
    assert len(rank_in_play(results, top_n=1)) == 1


def test_config_from_strategy_yaml_weights_sum_to_100():
    cfg = InPlayConfig.from_dict(load_strategy()["in_play"])
    assert sum(cfg.weights.values()) == 100
