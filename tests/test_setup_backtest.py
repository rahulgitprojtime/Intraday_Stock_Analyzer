"""Whole-market setup study, long and short (DECISIONS #31)."""

import csv
import random
from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.paper.costs import CostModel
from src.research import setup_backtest as sb
from src.research.setup_backtest import DayInput, K, Signal
from src.utils.config import load_yaml

COSTS = CostModel.from_dict(load_yaml("costs.yaml"))
S = sb.SLIPPAGE_BPS / 10_000


def five(t, o, h, l, c, v=1000):
    """Five 1-min bars that aggregate to one 5-min candle (o, h, l, c)."""
    return ([K(t, o, h, l, o, v)] + [K(t + i, o, o, o, o, v) for i in (1, 2, 3)]
            + [K(t + 4, o, max(o, c), min(o, c), c, v)])


def fifteen(t, o, h, l, c, v=1000):
    return ([K(t, o, h, l, o, v)] + [K(t + i, o, o, o, o, v) for i in range(1, 14)]
            + [K(t + 14, o, max(o, c), min(o, c), c, v)])


def flat_until(bars, end=375, px=None):
    px = bars[-1].c if px is None else px
    return bars + [K(t, px, px, px, px, 1000) for t in range(bars[-1].t + 1, end)]


def day_input(bars, prior=(), index=None, **kw):
    index = index or [K(t, 20000 + t, 20000 + t, 20000 + t, 20000 + t, 0) for t in range(375)]
    return sb.make_day(bars, list(prior), index, **kw)


# -- candles ---------------------------------------------------------------------------

def test_aggregate_aligns_to_the_open_and_mirror_commutes():
    bars = [K(t, 100 + t, 101 + t, 99 + t, 100.5 + t, 10) for t in range(12)]
    c5 = sb.aggregate(bars, 5)
    assert [c.t for c in c5] == [0, 5, 10]
    assert c5[0] == K(0, 100, 105, 99, 104.5, 50)
    assert c5[2].v == 20
    assert sb.aggregate(sb.mirror(bars), 5) == sb.mirror(c5)


def test_bullish_patterns_and_their_bearish_mirrors():
    assert sb.pattern(None, K(0, 100, 100.15, 97, 100.1, 1)) == "HAMMER"
    assert sb.pattern(K(0, 101, 101.2, 99.8, 100, 1), K(5, 99.9, 101.6, 99.8, 101.5, 1)) == "ENGULFING"
    assert sb.pattern(None, K(0, 100, 102, 99.9, 101.9, 1)) == "MARUBOZU"
    assert sb.pattern(None, K(0, 100, 101, 99, 100.2, 1)) is None          # spinning top
    star = K(0, 100, 103, 99.7, 99.8, 1)                                    # shooting star
    assert sb.pattern(None, star) is None
    assert sb.pattern(None, sb.mirror([star])[0]) == "HAMMER"
    assert sb.pattern_name("SHORT", "HAMMER") == "SHOOTING_STAR"


# -- setups ----------------------------------------------------------------------------

def orb_bars(green=True):
    o, c = (100.2, 100.9) if green else (100.9, 100.2)
    bars = five(0, o, 101, 100, c) + five(5, 100.9, 100.95, 100.5, 100.8) \
        + five(10, 100.85, 102, 100.8, 101.9)     # opens above the prior close: not an engulfing
    return flat_until(bars)


def test_orb_long_needs_a_green_first_candle_and_takes_the_first_close_above():
    [sig] = [s for s in sb.detect(day_input(orb_bars(), atr=5.0)) if s.setup == "ORB"]
    assert (sig.t, sig.ref, sig.pattern, sig.atr_stop) == (15, 101.9, "MARUBOZU", 0.5)
    red = day_input(orb_bars(green=False), atr=5.0)
    assert not [s for s in sb.detect(red) if s.setup == "ORB"]
    assert not [s for s in sb.detect(red.mirrored()) if s.setup == "ORB"]   # never closed below 100


def test_orb_short_is_the_mirror_on_a_red_first_candle():
    bars = flat_until(five(0, 100.9, 101, 100, 100.2) + five(5, 100.2, 100.5, 100.05, 100.1)
                      + five(10, 100.1, 100.2, 99, 99.1))
    [sig] = [s for s in sb.detect(day_input(bars, atr=5.0).mirrored()) if s.setup == "ORB"]
    assert (sig.t, sig.ref, sig.pattern) == (15, -99.1, "MARUBOZU")


def test_orb_without_atr_is_skipped():
    assert not [s for s in sb.detect(day_input(orb_bars(), atr=None)) if s.setup == "ORB"]


def test_vwap_cross_up_with_index_above_its_average():
    bars = [K(t, 100, 100.1, 99.9, 100, 1000) for t in range(15)]
    bars += five(15, 100, 100.05, 99.5, 99.6) + five(20, 99.6, 100.8, 99.55, 100.7)
    d = day_input(flat_until(bars))
    [sig] = [s for s in sb.detect(d) if s.setup == "VWAP"]
    assert (sig.t, sig.ref, sig.stop) == (25, 100.7, 99.55)
    assert sig.trend and all(T > 25 for T, _, _ in sig.trend)
    falling = [K(t, 20000 - t, 20000 - t, 20000 - t, 20000 - t, 0) for t in range(375)]
    assert not [s for s in sb.detect(day_input(flat_until(bars), index=falling)) if s.setup == "VWAP"]


def trend_prior(n=150, step=0.1):
    """Rising 5-min candles over two prior sessions (1-min bars, 75 candles each)."""
    days, c = [[], []], 100.0
    for k in range(n):
        o, c = c, c + step
        days[k // 75] += five((k % 75) * 5, o, c + 0.02, o - 0.02, c)
    return days, c


def test_ema_rejection_long_on_a_hammer_touching_ema5():
    prior, c = trend_prior()
    bars = []
    for k in range(5):
        o, c = c, c + 0.1
        bars += five(k * 5, o, c + 0.02, o - 0.02, c)
    o, c = c, c + 0.1
    bars += five(25, o, c + 0.01, c - 0.35, c)                  # hammer dipping to EMA5
    d = day_input(flat_until(bars), prior, daily_trend=1)
    [sig] = [s for s in sb.detect(d) if s.setup == "EMA"]
    assert (sig.t, sig.pattern) == (30, "HAMMER") and sig.stop == pytest.approx(c - 0.35)
    assert sig.mtf is True                                      # 10/30-min and daily all up
    d_down = day_input(flat_until(bars), prior, daily_trend=-1)
    assert [s for s in sb.detect(d_down) if s.setup == "EMA"][0].mtf is False


def test_bollinger_lower_band_pierce_closing_back_inside():
    prior = [[], []]
    for k in range(50):
        c = 100.0 if k % 2 else 100.2
        prior[k // 25] += fifteen((k % 25) * 15, c, c, c, c)
    bars = flat_until(fifteen(0, 100.0, 100.05, 99.0, 100.04))
    [sig] = [s for s in sb.detect(day_input(bars, prior)) if s.setup == "BB"]
    assert (sig.t, sig.stop, sig.pattern) == (15, 99.0, "HAMMER")
    assert sig.target == pytest.approx((10 * 100.0 + 9 * 100.2 + 100.04) / 20)   # middle band


def test_level_bounce_at_the_previous_day_low_and_rejection_at_its_high():
    bars = fifteen(0, 97, 97.2, 96.5, 96.6) + fifteen(15, 96.0, 96.15, 95.1, 96.1)
    d = day_input(flat_until(bars), pdh=105.0, pdl=95.0, pdc=100.0)
    [sig] = [s for s in sb.detect(d) if s.setup == "LEVEL"]
    assert (sig.t, sig.stop, sig.target, sig.pattern) == (30, 95.1, 100.0, "HAMMER")
    assert not [s for s in sb.detect(d.mirrored()) if s.setup == "LEVEL"]
    top = fifteen(0, 103, 103.5, 102.9, 103.4) + fifteen(15, 104.0, 104.9, 103.85, 103.9)
    dt = day_input(flat_until(top), pdh=105.0, pdl=95.0, pdc=100.0)
    [short] = [s for s in sb.detect(dt.mirrored()) if s.setup == "LEVEL"]
    assert (short.t, short.stop, short.target) == (30, -104.9, -100.0)


def test_candle_requirement_can_be_switched_off_for_the_control():
    bars = [K(t, 100, 100.1, 99.9, 100, 1000) for t in range(15)]
    bars += five(15, 100, 100.05, 99.5, 99.6) + five(20, 99.7, 101, 99.0, 100.2)  # no pattern
    d = day_input(flat_until(bars))
    assert not [s for s in sb.detect(d) if s.setup == "VWAP" and s.t == 25]
    assert [s for s in sb.detect(d, candle=False) if s.setup == "VWAP"][0].t == 25


# -- fills and exits ---------------------------------------------------------------------

def sig_at(t=10, stop=99.0, **kw):
    return Signal("VWAP", t, 100.0, stop, None, "MARUBOZU", **kw)


def test_entry_is_the_next_open_plus_slippage_and_a_wrong_side_stop_skips():
    bars = [K(t, 100, 100.5, 99.5, 100, 1) for t in range(20)]
    assert sb.enter(sig_at(), bars) == (10, round(100 * (1 + S), 4), 99.0)
    assert sb.enter(sig_at(stop=100.2), bars) is None
    assert sb.enter(sig_at(t=360), bars + [K(360, 100, 100, 100, 100, 1)]) is None


def test_stop_is_assumed_first_when_stop_and_target_touch_in_one_bar():
    bars = [K(10, 100, 100.2, 99.8, 100, 1), K(11, 100, 106, 98, 101, 1)]
    x = sb.exit_trade(bars, 0, 100.05, 99.0, target=105.0)
    assert (x.t, x.price, x.reason) == (11, round(99.0 * (1 - S), 4), "STOP")


def test_gap_through_the_stop_fills_at_the_open():
    bars = [K(10, 100, 100.2, 99.8, 100, 1), K(11, 98, 98.5, 97.5, 98, 1)]
    assert sb.exit_trade(bars, 0, 100.05, 99.0).price == round(98 * (1 - S), 4)


def test_target_needs_a_trade_through_and_fills_without_slippage():
    touch = [K(10, 100, 101, 99.8, 100.5, 1)] + [K(t, 100.5, 100.6, 100.4, 100.5, 1)
                                                  for t in range(11, 375)]
    x = sb.exit_trade(touch, 0, 100.0, 99.0, target=101.0)
    assert (x.reason, x.t) == ("SQUARE_OFF", 360)
    assert x.price == round(100.5 * (1 - S), 4)
    through = [K(10, 100, 101.2, 99.8, 101, 1)]
    assert sb.exit_trade(through, 0, 100.0, 99.0, target=101.0).price == 101.0


def test_trend_exit_fills_at_the_next_open_after_the_closing_bar():
    bars = [K(t, 100 + t * 0.01, 100.5, 99.5, 100, 1) for t in range(10, 40)]
    trend = ((15, 101.0, 100.0), (20, 99.9, 100.0), (25, 99.0, 100.0))
    x = sb.exit_trade(bars, 0, 100.0, 90.0, trend=trend)
    assert (x.t, x.reason, x.price) == (20, "TREND", round(bars[10].o * (1 - S), 4))


def test_slippage_matches_the_paper_broker_on_both_sides():
    from src.paper.fills import slip
    from src.paper.orders import Side
    for p in (100.5, 1234.55, 99.95, 2071.3):
        assert sb._buy(p) == slip(p, Side.BUY, 5) and sb._sell(p) == slip(p, Side.SELL, 5)
        assert -sb._buy(-p) == slip(p, Side.SELL, 5) and -sb._sell(-p) == slip(p, Side.BUY, 5)


def test_short_pnl_and_charges_are_on_the_right_order_sides():
    gross, charges, net = sb.settle("SHORT", 250, -100.0, -99.0, COSTS)
    assert gross == pytest.approx(250.0)
    expect = COSTS.charges("SELL", 250, 100.0).total + COSTS.charges("BUY", 250, 99.0).total
    assert charges == pytest.approx(expect)
    assert net == pytest.approx(gross - charges)
    assert COSTS.charges("SELL", 250, 100.0).stt > 0 and COSTS.charges("BUY", 250, 99.0).stamp_duty > 0


# -- no look-ahead -----------------------------------------------------------------------

def random_day(seed):
    rnd, px, days = random.Random(seed), 500.0, []
    for _ in range(3):
        bars = []
        for t in range(375):
            o = px
            px = max(1.0, px * (1 + rnd.gauss(0, 0.0015)))
            hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, 0.0005))), min(o, px) * (1 - abs(rnd.gauss(0, 0.0005)))
            bars.append(K(t, round(o, 2), round(hi, 2), round(lo, 2), round(px, 2), rnd.randint(100, 5000)))
        days.append(bars)
    index = [K(t, 20000 + 5 * rnd.gauss(0, 1) * t ** 0.5, 0, 0, 0, 0) for t in range(375)]
    index = [K(b.t, b.o, b.o, b.o, b.o, 0) for b in index]
    return days, index


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_signals_do_not_change_when_the_future_is_removed(seed):
    days, index = random_day(seed)
    prior, today = days[:2], days[2]
    kw = dict(pdh=max(b.h for b in prior[1]), pdl=min(b.l for b in prior[1]), pdc=prior[1][-1].c,
              atr=5.0, daily_trend=1)
    seen = 0
    for mirrored in (False, True):
        for candle in (True, False):
            full = day_input(today, prior, index, **kw)
            full = full.mirrored() if mirrored else full
            for sig in sb.detect(full, candle):
                cut = day_input([b for b in today if b.t < sig.t], prior,
                                [b for b in index if b.t < sig.t], **kw)
                cut = cut.mirrored() if mirrored else cut
                again = [s for s in sb.detect(cut, candle) if s.setup == sig.setup]
                assert again and replace(again[0], trend=()) == replace(sig, trend=()), sig
                seen += 1
    assert seen >= 4


# -- end to end ----------------------------------------------------------------------------

def write_store(root, days):
    daily = root / "daily"
    daily.mkdir(parents=True)
    with (daily / "AAA.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        d = days[0] - timedelta(days=40)
        while d < days[0]:
            w.writerow([d.isoformat(), 100, 102, 98, 100, 100000])
            d += timedelta(days=1)
    for i, day in enumerate(days):
        folder = root / day.isoformat()
        folder.mkdir()
        bars = orb_bars() if i == len(days) - 1 else flat_until(five(0, 100, 100.5, 99.5, 100))
        for name, rows in (("AAA", bars),
                           ("NIFTY", [K(t, 20000 + t, 20000 + t, 20000 + t, 20000 + t, 0)
                                      for t in range(375)])):
            with (folder / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "open", "high", "low", "close", "volume", "is_complete"])
                for b in rows:
                    hh, mm = divmod(555 + b.t, 60)
                    w.writerow([f"{day.isoformat()}T{hh:02d}:{mm:02d}:00", b.o, b.h, b.l, b.c, b.v, 1])


def test_run_chunk_writes_one_row_per_signal_with_every_exit(tmp_path):
    days = [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
    write_store(tmp_path / "store", days)
    out = tmp_path / "trades.csv"
    counts = sb.run_chunk(str(tmp_path / "store"), [d.isoformat() for d in days[2:]],
                          [d.isoformat() for d in days[:2]], str(out), load_yaml("costs.yaml"), 0.02)
    assert counts["stock_days"] == 1
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    orb = [r for r in rows if r["setup"] == "ORB" and r["side"] == "LONG" and r["candle"] == "1"]
    assert len(orb) == 1
    r = orb[0]
    assert r["pattern"] == "MARUBOZU" and r["entry_time"] == "09:30"
    assert float(r["qty"]) == 25_000 // 101.9
    for e in sb.EXITS:
        assert r[f"{e}_reason"] in ("STOP", "TARGET", "TREND", "SQUARE_OFF")
    summary = sb.summarize(rows)
    m = summary[("ORB", "LONG", "NATIVE", "ALL")]
    assert m["trades"] == 1 and m["net"] == pytest.approx(float(r["NATIVE_net"]), abs=0.01)
    gross = float(r["NATIVE_net"]) + float(r["NATIVE_charges"])
    assert r["day_type"] == "UP" and float(r["rvol5"]) == 2.5         # 5,000 / (100,000 x 2%)
    assert m["avg_gross_day"]["UP"] == (1, pytest.approx(gross, abs=0.01))
    assert m["avg_gross_rvol"]["2-5"] == (1, pytest.approx(gross, abs=0.01))


def test_summary_pass_rule():
    m = sb.metrics([10.0] * 60 + [-5.0] * 50, days=["d"] * 110, charges=[1.0] * 110)
    assert m["trades"] == 110 and m["pf"] == pytest.approx(600 / 250)
    assert sb.passes(m)
    assert not sb.passes(sb.metrics([10.0] * 60, days=["d"] * 60, charges=[1.0] * 60))
