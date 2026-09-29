"""Live paper session in the worker (DECISIONS #29): ticks fill, bars drive the strategy."""

import json
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from src.app.paper_live import build_live_paper
from src.data.models import Candle, Exchange, Instrument, Segment
from src.paper.ledger import Ledger
from src.storage.candle_cache import IntradayCandleCache
from src.utils.config import load_yaml

DAY = date(2026, 9, 25)
AAA = Instrument("AAA", Exchange.NSE, Segment.CASH)


def at(h, m, s=0):
    return datetime.combine(DAY, time(h, m, s))


class FakeFeed:
    def __init__(self):
        self.ltp = {}

    def snapshot(self, now):
        return SimpleNamespace(symbols={k: SimpleNamespace(ltp=v) for k, v in self.ltp.items()})


def ctx_with_bars(tmp_path, until=time(9, 31)):
    cache = IntradayCandleCache(tmp_path / "cache")
    bars, t = [], at(9, 15)
    while t.time() < until:
        o, h, l, c = (100.8, 101.6, 100.7, 101.5) if t.time() == time(9, 30) else (100, 101, 99, 100)
        bars.append(Candle(AAA, 1, t, o, h, l, c, 1000))
        t += timedelta(minutes=1)
    cache.save(AAA, DAY, bars)
    return SimpleNamespace(stocks=[AAA], source=SimpleNamespace(cache=cache), preps={},
                           feed_store=FakeFeed())


def raw(tmp_path, **kw):
    return load_yaml("paper.yaml") | {"ledger": str(tmp_path / "paper.sqlite")} | kw


def test_orb_live_paper_fills_on_the_next_tick_and_squares_off(tmp_path):
    ctx = ctx_with_bars(tmp_path)
    live = build_live_paper(ctx, DAY, raw(tmp_path, strategy="orb"), now=at(9, 0))
    live.on_minute(ctx, at(9, 31), {})                     # bars 09:15..09:30 closed
    broker = live.session.broker
    assert [o.tag for o in broker.open_orders()] == ["ORB_ENTRY"] and broker.positions() == {}
    ctx.feed_store.ltp["AAA"] = 101.7
    assert live.pump(at(9, 31, 3)) == 1
    assert live.pump(at(9, 31, 4)) == 0                    # unchanged price: not a new tick
    assert broker.positions()["AAA"].quantity > 0
    live.stop(DAY)
    db = Ledger(tmp_path / "paper.sqlite")
    [run] = db.runs()
    assert run["mode"] == "PAPER" and run["strategy"] == "ORB"
    [t] = db.trades(run["run_id"])
    assert (t["entry_price"], t["exit_tag"]) == (round(101.7 * 1.0005, 4), "SQUARE_OFF")  # 5 bps


def test_recommendation_live_paper_journals_the_entry(tmp_path):
    ctx = ctx_with_bars(tmp_path)
    live = build_live_paper(ctx, DAY, raw(tmp_path, strategy="recommendation"), now=at(9, 0))
    rec = {"symbol": "AAA", "category": "CANDIDATE", "score": 70.0, "rank": 1,
           "eligible_for_top_n": True, "setup": {"best": "ORB15", "best_state": "TRIGGERED"},
           "quantitative": {"is_in_play": True}, "data_quality": {"status": "OK"}}
    live.on_minute(ctx, at(9, 31), {"modes": {"DAY": [rec]}})
    ctx.feed_store.ltp["AAA"] = 101.0
    live.pump(at(9, 31, 2))
    live.stop(DAY)
    events = [json.loads(x) for x in
              (tmp_path / "journal" / f"{DAY}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["ENTRY", "EXIT"]
    assert events[0]["replay_or_live"] == "live" and events[0]["stop_method"] == "pct_fallback"


def test_a_restart_starts_a_new_run_and_keeps_the_old_one(tmp_path):
    ctx = ctx_with_bars(tmp_path)
    build_live_paper(ctx, DAY, raw(tmp_path, strategy="orb"), now=at(9, 0)).stop(DAY)
    build_live_paper(ctx, DAY, raw(tmp_path, strategy="orb"), now=at(9, 40)).stop(DAY)
    assert len(Ledger(tmp_path / "paper.sqlite").runs()) == 2
