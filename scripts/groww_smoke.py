"""Read-only smoke test of the Groww adapter against the real API (M1/M7).

Loads credentials from `.env` (never printed). Calls only market-data
methods: auth, instrument master, LTP, OHLC, quote, 1-min history.
No orders — the adapter has no order methods (DECISIONS #8).

    python scripts/groww_smoke.py
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.broker.groww import GrowwAdapter  # noqa: E402
from src.broker.groww_instruments import InstrumentMaster  # noqa: E402
from src.data.models import HistoricalCandleRequest  # noqa: E402
from src.utils.config import load_settings  # noqa: E402


def step(name, fn):
    try:
        result = fn()
        print(f"PASS  {name}: {result}")
        return True
    except Exception as exc:  # report every step; one failure must not hide the rest
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=2, file=sys.stdout)
        return False


def main() -> int:
    load_dotenv(ROOT / ".env")
    storage = load_settings()["storage"]
    data_dir = ROOT / storage["data_dir"]
    master = InstrumentMaster.load(data_dir / storage["instrument_cache_file"],
                                   storage["instrument_cache_max_age_hours"])
    adapter = GrowwAdapter(master)
    ok = step("authenticate", lambda: adapter.authenticate() or "authenticated")
    if not ok:
        return 1
    rel = adapter.resolve_instrument("RELIANCE", "NSE", "CASH")
    nifty = adapter.resolve_instrument("NIFTY", "NSE", "CASH")
    results = [
        step("resolve", lambda: f"{rel.trading_symbol} token={rel.exchange_token}, "
                                f"{nifty.trading_symbol} is_index={nifty.is_index}"),
        step("get_ltp", lambda: adapter.get_ltp([rel, nifty])),
        step("get_ohlc", lambda: {k: (v.open, v.high, v.low, v.close)
                                  for k, v in adapter.get_ohlc([rel]).items()}),
        step("get_quote", lambda: (lambda q: f"last={q.last_price} vol={q.volume} "
                                   f"depth={'yes' if q.depth else 'no'}")(adapter.get_quote(rel))),
    ]
    today = date.today()
    for label, inst in (("RELIANCE", rel), ("NIFTY", nifty)):
        req = HistoricalCandleRequest(
            inst, datetime.combine(today - timedelta(days=5), time(9, 15)),
            datetime.combine(today, time(15, 30)), interval_minutes=1)

        def hist(req=req):
            candles = adapter.get_historical_candles(req)
            if not candles:
                return "0 candles"
            days = sorted({c.timestamp.date() for c in candles})
            return (f"{len(candles)} candles over {len(days)} sessions, "
                    f"first={candles[0].timestamp} last={candles[-1].timestamp}, "
                    f"last volume={candles[-1].volume}")

        results.append(step(f"1-min history {label}", hist))
    print(f"\n{sum(results) + 1}/{len(results) + 1} steps passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
