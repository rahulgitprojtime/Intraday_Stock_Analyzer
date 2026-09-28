"""Read-only smoke test of the live feed (M7) against the real Groww API.

Subscribes LTP + depth for a few stocks and the NIFTY index value for
`--seconds`, then prints tick counts, depth shape and the computed spread
/ imbalance / velocity. Market-data only; no orders (DECISIONS #8).
Run during market hours (09:15-15:30 IST).

    python scripts/feed_smoke.py --seconds 120
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.broker.groww import GrowwAdapter  # noqa: E402
from src.broker.groww_feed import LiveFeed  # noqa: E402
from src.broker.groww_instruments import InstrumentMaster  # noqa: E402
from src.data.feed_store import FeedStore  # noqa: E402
from src.quantitative.microstructure import MicroConfig, symbol_feed  # noqa: E402
from src.utils.config import load_settings  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seconds", type=int, default=120)
    p.add_argument("--symbols", default="RELIANCE,TCS,INFY")
    a = p.parse_args(argv)

    load_dotenv(ROOT / ".env")          # credentials; never printed
    storage = load_settings()["storage"]
    master = InstrumentMaster.load(ROOT / storage["data_dir"] / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    adapter.authenticate()
    stocks = [adapter.resolve_instrument(s, "NSE", "CASH") for s in a.symbols.split(",")]
    nifty = adapter.resolve_instrument("NIFTY", "NSE", "CASH")
    print("index exchange_token:", nifty.exchange_token)
    store = FeedStore()
    feed = LiveFeed(adapter.api_client, stocks, nifty, store)
    feed.start()
    print(f"subscribed {feed.subscribed} topics; collecting {a.seconds}s ...")
    try:
        end = time.time() + a.seconds
        while time.time() < end:
            time.sleep(10)
            snap = store.snapshot(datetime.now())
            print(f"{datetime.now():%H:%M:%S} last tick age={snap.last_any_tick_age_s} "
                  f"ticks={ {k: v.ticks_1m for k, v in snap.symbols.items()} }")
    finally:
        feed.stop()
    now = datetime.now()
    snap = store.snapshot(now)
    print(f"\nbad payloads={snap.bad_payloads} last_error={feed.last_error}")
    ok = True
    for sym in [s.trading_symbol for s in stocks] + ["NIFTY"]:
        st = snap.symbols.get(sym)
        if st is None:
            print(f"FAIL  {sym}: no data")
            ok = False
            continue
        f = symbol_feed(snap, sym, 120, MicroConfig())
        d = st.depth
        depth = (f"depth bidQ={d.bid_qty:.0f} askQ={d.ask_qty:.0f} "
                 f"totals={d.total_bid_qty:.0f}/{d.total_ask_qty:.0f} age={st.depth_age_s:.1f}s"
                 if d else "no depth")
        metrics = (f"spread={f.spread_pct} imbalance={f.imbalance} velocity={f.tick_velocity} "
                   f"micro={f.micro_score}" if f else "stale")
        print(f"{'PASS' if st.ticks_1m or st.ltp else 'FAIL'}  {sym}: ticks_1m={st.ticks_1m} "
              f"ltp_seen={st.ltp is not None} {depth} | {metrics}")
        ok &= bool(st.ltp is not None)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
