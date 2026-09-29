import pytest

from src.paper.costs import CostModel
from src.utils.config import load_yaml

COSTS = CostModel.from_dict(load_yaml("costs.yaml"))


def test_buy_side_charges_nse():
    c = COSTS.charges("BUY", 100, 1000.0, "NSE")          # turnover 1,00,000
    assert c.brokerage == 20.0                             # Rs 20 < 0.1% (Rs 100)
    assert c.stt == 0.0                                    # intraday STT is sell side only
    assert c.stamp_duty == pytest.approx(3.0)              # 0.003% buy side
    assert c.exchange_txn == pytest.approx(2.97)           # 0.00297%
    assert c.sebi_fee == pytest.approx(0.1) and c.ipft == pytest.approx(0.1)
    assert c.gst == pytest.approx(0.18 * (20 + 2.97 + 0.1 + 0.1))
    assert c.total == pytest.approx(20 + 2.97 + 0.1 + 0.1 + 4.1706 + 3.0, abs=1e-4)


def test_sell_side_charges_nse():
    c = COSTS.charges("SELL", 100, 1000.0, "NSE")
    assert c.stt == pytest.approx(25.0)                    # 0.025% sell side
    assert c.stamp_duty == 0.0
    assert c.total == pytest.approx(20 + 2.97 + 0.1 + 0.1 + 4.1706 + 25.0, abs=1e-4)


def test_small_order_brokerage_is_pct_with_rs5_minimum():
    assert COSTS.charges("BUY", 10, 1000.0).brokerage == pytest.approx(10.0)   # 0.1% of 10k
    assert COSTS.charges("BUY", 1, 100.0).brokerage == 5.0                     # floor Rs 5


def test_bse_uses_its_own_exchange_rate_and_no_ipft():
    c = COSTS.charges("BUY", 100, 1000.0, "BSE")
    assert c.exchange_txn == pytest.approx(3.75) and c.ipft == 0.0


def test_components_sum_to_total():
    c = COSTS.charges("SELL", 37, 1234.5, "NSE")
    parts = c.brokerage + c.stt + c.exchange_txn + c.sebi_fee + c.ipft + c.gst + c.stamp_duty
    assert c.total == pytest.approx(parts, abs=1e-4)


def test_rates_come_from_config():
    raw = load_yaml("costs.yaml")
    free = CostModel.from_dict(raw | {"stt_sell_pct": 0.0})
    assert free.charges("SELL", 100, 1000.0).stt == 0.0


def test_rejects_bad_side_and_quantity():
    with pytest.raises(ValueError):
        COSTS.charges("HOLD", 1, 100.0)
    with pytest.raises(ValueError):
        COSTS.charges("BUY", 0, 100.0)
