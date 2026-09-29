"""Intraday playbook (DECISIONS #30): one fixture scenario per setup + risk rules."""

from datetime import date, datetime, time, timedelta

import pytest

from src.indicators.core import ema
from src.paper.backtest import run_day
from src.paper.broker import BacktestBroker, BrokerConfig
from src.paper.costs import CostModel
from src.paper.orders import Bar, OrderStatus, OrderType
from src.paper.session import TradingSession
from src.paper.shortlist import ShortlistConfig
from src.paper.strategies.playbook import IntradayPlaybook, PlaybookConfig
from src.utils.config import load_yaml

RAW = load_yaml("costs.yaml")
FREE = CostModel.from_dict(RAW | {
    "brokerage": {"flat_per_order": 0, "pct": 0, "min_per_order": 0}, "stt_sell_pct": 0,
    "stamp_duty_buy_pct": 0, "exchange_txn_pct": {"NSE": 0}, "sebi_fee_pct": 0,
    "ipft_pct": {"NSE": 0}})
BROKER = BrokerConfig(100_000, 25_000, 5, 0, time(15, 15))
DAY, PREV = date(2026, 9, 25), date(2026, 9, 24)
OPEN = time(9, 15)
LOOSE = ShortlistConfig(top_n=15, min_turnover=0)


def expand(sym, day, specs, width=5, start=OPEN):
    """1-min bars whose `width`-min aggregates are exactly `specs` (o, h, l, c, v)."""
    out, t = [], datetime.combine(day, start)
    for o, h, l, c, v in specs:
        for i in range(width):
            out.append(Bar(sym, t, o if i == 0 else c, h, l, c, v / width))
            t += timedelta(minutes=1)
    return out


def flat_session(sym, day, price=100.0, vol=1000, width=5, n=75):
    return expand(sym, day, [(price, price, price, price, vol)] * n, width)


def play(setups, today, prior=None, **kw):
    cfg = PlaybookConfig(setups=tuple(setups), shortlist=kw.pop("shortlist", LOOSE), **kw)
    strat = IntradayPlaybook(cfg)
    broker = BacktestBroker(BROKER, FREE)
    s = TradingSession(strat, broker)
    for sym, bars in (prior or {}).items():
        s.ctx.set_prior(sym, bars)
    run_day(s, DAY, [b for bars in today.values() for b in bars])
    return strat, broker


def entries(broker):
    return [o for o in broker.orders() if o.tag in ("ORB", "VWAP", "EMA_REJECTION",
                                                     "BB_REVERSAL", "PDL_BOUNCE")]


# -- ORB -------------------------------------------------------------------------------

RANGE = [(100, 101, 99, 100, 1000)] * 3                  # 09:15-09:30: range 99-101


def orb_day(breakout_vol=5000, after=102.0, sym="AAA"):
    specs = RANGE + [(100.8, 101.6, 100.7, 101.5, breakout_vol),       # 09:30 bar closes 09:35
                     (101.6, 102, 101.4, 101.9, 1000)] + [(after, after + .2, after - .2, after,
                                                           1000)] * 70
    return expand(sym, DAY, specs)


def test_orb_long_on_first_5min_close_above_the_first_15min_high():
    strat, broker = play(["ORB"], {"AAA": orb_day()})
    [o] = entries(broker)
    assert (o.type, o.fill_price, o.filled_at) == (OrderType.MARKET, 101.6,
                                                   datetime.combine(DAY, time(9, 35)))
    [t] = broker.trades
    risk = 101.5 - 101                                    # stop = range high (source's rule)
    assert t["quantity"] == min(int(1000 // risk), int(25_000 // 101.5))
    assert strat.signals[0]["action"] == "order placed"


def test_orb_needs_above_average_volume():
    _, broker = play(["ORB"], {"AAA": orb_day(breakout_vol=500)})
    assert entries(broker) == []


def test_orb_skips_a_too_wide_opening_range():
    wide = expand("AAA", DAY, [(100, 104, 99, 100, 1000)] + RANGE[1:] +
                  [(101, 105, 100.9, 104.5, 9000)] + [(104, 104, 104, 104, 1000)] * 71)
    _, broker = play(["ORB"], {"AAA": wide})
    assert entries(broker) == []


def test_orb_respects_india_vix_when_available():
    vix = flat_session("INDIAVIX", DAY, price=22.0)
    _, broker = play(["ORB"], {"AAA": orb_day(), "INDIAVIX": vix})
    assert entries(broker) == []                          # VIX 22 outside 12-18


# -- VWAP ------------------------------------------------------------------------------

def vwap_day(sym="AAA"):
    down = [(100 - i * .3, 100 - i * .3 + .1, 100 - i * .3 - .4, 100 - i * .3 - .3, 1000)
            for i in range(6)]                           # sliding under VWAP to ~98
    cross = [(98.2, 100.4, 98.1, 100.3, 3000)]           # 09:45 bar closes above VWAP
    return expand(sym, DAY, down + cross + [(100.3, 100.5, 100.1, 100.3, 1000)] * 68)


def index_day(rising=True):
    step = .2 if rising else -.2
    return expand("NIFTY", DAY, [(25000 + i * step, 25000 + i * step + .1,
                                  25000 + i * step - .1, 25000 + i * step, 0) for i in range(75)])


def test_vwap_cross_with_rising_volume_and_index_above_its_average():
    _, broker = play(["VWAP"], {"AAA": vwap_day(), "NIFTY": index_day(True)})
    [o] = entries(broker)
    assert o.filled_at == datetime.combine(DAY, time(9, 50))
    assert o.symbol == "AAA"                              # the index is never traded


def test_vwap_needs_the_index_above_its_own_average():
    _, broker = play(["VWAP"], {"AAA": vwap_day(), "NIFTY": index_day(False)})
    assert entries(broker) == []


# -- EMA 5/15 rejection -------------------------------------------------------------------

def rising(day, n, start=100.0, step=.1):
    return [(start + i * step, start + i * step + .05, start + i * step - .05,
             start + i * step, 1000) for i in range(n)]


def ema_day(follow_through=True):
    prior = rising(PREV, 75)
    today = rising(DAY, 5, start=100 + 75 * .1)          # 09:15-09:40, still rising
    closes = [s[3] for s in prior + today]
    o = closes[-1] + .05
    c = o + .02
    fast = ema(closes + [c], 5)[-1]
    rejection = (o, c + .01, fast - .05, c, 1000)        # hammer touching the fast EMA
    nxt_hi = c + .5 if follow_through else c
    nxt = (c, nxt_hi, c - .01, c + .1 if follow_through else c - .005, 1000)
    rest = [(nxt[3], nxt[3] + .05, nxt[3] - .05, nxt[3], 1000)] * 68
    return expand("AAA", PREV, prior), expand("AAA", DAY, today + [rejection, nxt] + rest), \
        rejection


def test_ema_rejection_buys_above_the_rejection_high():
    prior, today, rej = ema_day()
    _, broker = play(["EMA_REJECTION"], {"AAA": today}, {"AAA": prior})
    [o] = entries(broker)
    assert (o.type, o.trigger_price) == (OrderType.STOP, round(rej[1], 4))
    assert o.status is OrderStatus.FILLED and o.fill_price == pytest.approx(rej[1])


def test_ema_rejection_buy_stop_expires_after_one_bar():
    prior, today, _ = ema_day(follow_through=False)
    _, broker = play(["EMA_REJECTION"], {"AAA": today}, {"AAA": prior})
    [o] = entries(broker)
    assert o.status is OrderStatus.CANCELLED and broker.trades == []


def test_ema_rejection_needs_rising_emas():
    prior = expand("AAA", PREV, [(100, 100.05, 99.95, 100, 1000)] * 75)       # flat EMAs
    today = expand("AAA", DAY, [(100, 100.02, 99.7, 100.01, 1000)] + [(100, 100.5, 99.9, 100.2,
                                                                        1000)] * 74)
    _, broker = play(["EMA_REJECTION"], {"AAA": today}, {"AAA": prior})
    assert entries(broker) == []


# -- Bollinger reversal ---------------------------------------------------------------------

def bb_prior():
    return expand("AAA", PREV, [(100.5, 101.2, 99.8, 100 + i % 2, 1000) for i in range(25)], 15)


def test_bb_reversal_on_a_green_bar_touching_the_lower_band():
    touch = (99.8, 100.2, 99.3, 100.0, 1000)             # low under the band, high above it
    today = expand("AAA", DAY, [touch, (100.1, 100.6, 100.0, 100.4, 1000)] +
                   [(100.4, 100.6, 100.2, 100.4, 1000)] * 23, 15)
    _, broker = play(["BB_REVERSAL"], {"AAA": today}, {"AAA": bb_prior()})
    [o] = entries(broker)
    assert (o.trigger_price, o.status) == (100.2, OrderStatus.FILLED)
    stop = next(x for x in broker.orders() if x.tag == "STOP_LOSS")
    assert stop.trigger_price == pytest.approx(100.2 - (100.2 - 99.3))


def test_bb_whole_bar_below_the_band_is_a_breakdown_not_a_bounce():
    below = (99.0, 99.2, 98.0, 99.1, 1000)
    today = expand("AAA", DAY, [below] + [(99.1, 99.3, 98.9, 99.1, 1000)] * 24, 15)
    _, broker = play(["BB_REVERSAL"], {"AAA": today}, {"AAA": bb_prior()})
    assert entries(broker) == []


# -- previous-day-low bounce ------------------------------------------------------------------

def pdl_prior(pdh=105.0):
    return expand("AAA", PREV, [(96, pdh, 95, 96, 1000)] + [(96, 96, 96, 96, 1000)] * 24, 15)


def pdl_today():
    hammer = (95.9, 96.0, 95.1, 95.95, 5000)             # low 0.1% above PDL 95
    return expand("AAA", DAY, [hammer, (95.95, 97, 95.9, 96.8, 1000)] +
                  [(96.8, 97, 96.6, 96.8, 1000)] * 23, 15)


def test_pdl_bounce_targets_the_previous_day_high():
    _, broker = play(["PDL_BOUNCE"], {"AAA": pdl_today()}, {"AAA": pdl_prior()})
    [o] = entries(broker)
    assert o.trigger_price == 96.0 and o.status is OrderStatus.FILLED
    target = next(x for x in broker.orders() if x.tag == "TARGET")
    assert target.limit_price == pytest.approx(105.0)    # fill 96 + (PDH - trigger)


def test_pdl_bounce_skipped_when_pdh_is_under_2r():
    strat, broker = play(["PDL_BOUNCE"], {"AAA": pdl_today()}, {"AAA": pdl_prior(pdh=97.0)})
    assert entries(broker) == []
    assert strat.signals[0]["action"] == "skipped: level target under 2R"


# -- shortlist and risk rules -------------------------------------------------------------------

def test_only_shortlisted_stocks_trade_and_rank_orders_same_minute_signals():
    today = {s: orb_day(sym=s) for s in ("AAA", "BBB", "CCC")}
    today["BBB"] = [Bar(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume * 10)
                    for b in today["BBB"]]               # most turnover: shortlist rank 1
    strat, broker = play(["ORB"], today, shortlist=ShortlistConfig(top_n=2, min_turnover=0))
    assert [r["symbol"] for r in strat.shortlist][0] == "BBB"
    assert [o.symbol for o in entries(broker)] == [r["symbol"] for r in strat.shortlist]


def test_max_three_trades_per_day():
    today = {s: orb_day(sym=s) for s in ("A1", "A2", "A3", "A4")}
    strat, broker = play(["ORB"], today)
    assert len(entries(broker)) == 3
    assert [s["action"] for s in strat.signals].count("skipped: max 3 trades/day") == 1


def test_two_losses_in_a_row_stop_the_day():
    losers = {s: expand(s, DAY, RANGE + [(100.8, 101.6, 100.7, 101.5, 5000),
                                         (101.6, 101.6, 99, 99.5, 1000)] +
                        [(99.5, 99.6, 99.4, 99.5, 1000)] * 70) for s in ("L1", "L2")}
    late = expand("LATE", DAY, RANGE * 3 + [(100.8, 101.6, 100.7, 101.5, 9000)] +
                  [(101.6, 102, 101.4, 101.9, 1000)] * 65)   # breaks out at 10:00
    strat, broker = play(["ORB"], losers | {"LATE": late})
    assert [t["exit_tag"] for t in broker.trades] == ["STOP_LOSS", "STOP_LOSS"]
    assert strat.halted == "2 losses in a row"
    assert "LATE" not in {o.symbol for o in entries(broker)}


def test_no_entries_before_0930():
    early = expand("AAA", DAY, [(100, 100.1, 99.9, 100, 1000)] * 75)
    strat, _ = play(list(PlaybookConfig().setups), {"AAA": early})
    assert all(s["at"] >= datetime.combine(DAY, time(9, 30)).isoformat() for s in strat.signals)


def test_config_from_paper_yaml_and_unknown_setup():
    cfg = PlaybookConfig.from_dict(load_yaml("paper.yaml")["playbook"])
    assert cfg.risk_pct == 1.0 and cfg.min_rr == 2.0 and cfg.max_trades_per_day == 3
    assert cfg.shortlist.top_n == 15
    with pytest.raises(ValueError):
        PlaybookConfig.from_dict({"setups": ["SHORT_EVERYTHING"]})
