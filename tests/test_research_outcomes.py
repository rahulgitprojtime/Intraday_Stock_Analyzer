from datetime import datetime, timedelta

import pytest

from src.data.models import Candle, Exchange, Instrument, Segment
from src.research.outcomes import label_row

DAY = datetime(2026, 9, 29, 9, 15)


def bars(sym, start, closes, step=0.0):
    inst = Instrument(sym, Exchange.NSE, Segment.CASH)
    out, t = [], start
    for i, c in enumerate(closes):
        o = c - step
        out.append(Candle(inst, 1, t, o, max(o, c) + 0.5, min(o, c) - 0.5, c, 100))
        t += timedelta(minutes=1)
    return out


def loader(table):
    return lambda sym: table.get(sym, [])


def test_entry_is_the_snapshot_minute_open_and_returns_per_horizon():
    # AAA opens 100 at 09:30 and closes +1 each minute; NIFTY flat; sector +0.5%/5 min
    t0 = DAY + timedelta(minutes=15)
    table = {"AAA": bars("AAA", t0, [101 + i for i in range(70)], step=1.0),
             "NIFTY": bars("NIFTY", t0, [1000.0] * 70),
             "NIFTYAUTO": bars("NIFTYAUTO", t0, [100.0 + 0.1 * (i + 1) for i in range(70)], 0.1)}
    row = {"as_of": t0.isoformat(), "symbol": "AAA", "sector_index": "NIFTYAUTO"}
    out = label_row(row, loader(table), horizons=(5, 15), cost_pct=0.1)
    assert out["fwd_5"] == pytest.approx(5.0)            # entry 100 (open 09:30), close 09:34 = 105
    assert out["net_5"] == pytest.approx(4.9)
    assert out["mfe_5"] == pytest.approx(5.5) and out["mae_5"] == pytest.approx(-0.5)
    assert out["xs_nifty_5"] == pytest.approx(5.0)
    assert out["xs_sector_5"] == pytest.approx(5.0 - 0.5)
    assert out["fwd_15"] == pytest.approx(15.0) and out["truncated_15"] is False


def test_no_look_ahead_label_uses_only_bars_from_the_snapshot_minute():
    t0 = DAY + timedelta(minutes=15)
    before = bars("AAA", t0 - timedelta(minutes=5), [500.0] * 5)      # earlier bars never used
    table = {"AAA": before + bars("AAA", t0, [101 + i for i in range(10)], step=1.0)}
    out = label_row({"as_of": t0.isoformat(), "symbol": "AAA"}, loader(table), (5,), 0.0)
    assert out["fwd_5"] == pytest.approx(5.0) and out["xs_nifty_5"] is None


def test_windows_past_1525_are_truncated_not_stretched():
    t0 = datetime(2026, 9, 29, 15, 15)
    table = {"AAA": bars("AAA", t0, [100.0] * 15)}
    out = label_row({"as_of": t0.isoformat(), "symbol": "AAA"}, loader(table), (5, 15), 0.0)
    assert out["fwd_5"] == pytest.approx(0.0) and out["truncated_5"] is False
    assert out["fwd_15"] is None and out["truncated_15"] is True


def test_missing_bars_give_none_never_a_guess():
    t0 = DAY + timedelta(minutes=15)
    table = {"AAA": bars("AAA", t0, [100.0] * 3)}
    out = label_row({"as_of": t0.isoformat(), "symbol": "AAA"}, loader(table), (5,), 0.0)
    assert out["fwd_5"] is None and out["truncated_5"] is False
    assert label_row({"as_of": t0.isoformat(), "symbol": "ZZZ"}, loader(table), (5,), 0.0)["fwd_5"] is None
