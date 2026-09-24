import os
import time

import pytest

from src.broker.groww import GrowwAdapter
from src.broker.groww_instruments import InstrumentMaster
from src.data.universe import resolve_universe

# Header + rows copied from the real Groww instrument.csv (2026-09-25).
CSV = """exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,underlying_symbol,underlying_exchange_token,expiry_date,strike_price,lot_size,tick_size,freeze_quantity,is_reserved,buy_allowed,sell_allowed,internal_trading_symbol,is_intraday
NSE,66509,360ONE26NOV840CE,NSE-360ONE-23Nov26-840-CE,,CE,FNO,,,360ONE,13061,2026-11-23,840,500,0.05,20001,1,1,1,360ONE26NOV840CE,0
NSE,2885,RELIANCE,NSE-RELIANCE,Reliance Industries,EQ,CASH,EQ,INE002A01018,,,,,1,0.1,,,1,1,RELIANCE-EQ,1
BSE,500325,RELIANCE,BSE-RELIANCE,Reliance Industries,EQ,CASH,A,INE002A01018,,,,,1,0.05,,,1,1,RELIANCE,1
NSE,NIFTY,NIFTY,NSE-NIFTY,NIFTY 50,IDX,CASH,,NIFTY,,,,,,,,,0,0,,0
NSE,9999,SMECO,NSE-SMECO,Some SME Co,EQ,CASH,SM,INE000000001,,,,,1,0.05,,,1,1,SMECO-SM,1
"""


@pytest.fixture
def master():
    return InstrumentMaster.from_csv_text(CSV)


def test_master_keeps_only_cash_eq_and_idx(master):
    assert len(master) == 4  # FNO option row dropped
    assert master.get_row("360ONE26NOV840CE", "NSE") is None


def test_resolve_returns_exchange_token_and_metadata(master):
    inst = master.resolve("reliance", "NSE")
    assert inst.exchange_token == "2885"
    assert inst.isin == "INE002A01018"
    assert inst.name == "Reliance Industries"
    assert inst.series == "EQ" and not inst.is_index
    assert master.resolve("RELIANCE", "BSE").exchange_token == "500325"
    assert master.resolve("NIFTY").is_index


def test_resolve_unknown_symbol_raises(master):
    with pytest.raises(KeyError):
        master.resolve("NOPE")


def test_adapter_uses_master_for_exchange_token(master):
    adapter = GrowwAdapter(master)
    assert adapter.resolve_instrument("RELIANCE", "NSE", "CASH").exchange_token == "2885"
    with pytest.raises(ValueError):
        adapter.resolve_instrument("NOPE", "NSE", "CASH")


def test_load_downloads_when_cache_missing_then_reuses_cache(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return CSV

    path = tmp_path / "instruments.csv"
    assert len(InstrumentMaster.load(path, fetch=fetch)) == 4
    assert len(InstrumentMaster.load(path, fetch=fetch)) == 4
    assert len(calls) == 1


def test_load_refreshes_stale_cache_and_falls_back_on_failure(tmp_path):
    path = tmp_path / "instruments.csv"
    path.write_text(CSV, encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))

    def failing_fetch(url):
        raise OSError("network down")

    # stale cache still usable when refresh fails
    assert len(InstrumentMaster.load(path, fetch=failing_fetch)) == 4
    # but with no cache at all, the failure propagates
    with pytest.raises(OSError):
        InstrumentMaster.load(tmp_path / "missing.csv", fetch=failing_fetch)


def test_resolve_universe_filters_and_reports(master):
    adapter = GrowwAdapter(master)
    config = {
        "exchange": "NSE",
        "allowed_series": ["EQ"],
        "symbols": ["RELIANCE", "reliance", "SMECO", "NIFTY", "NOPE"],
        "indices": ["NIFTY", "RELIANCE"],
    }
    u = resolve_universe(config, lambda s, e: adapter.resolve_instrument(s, e, "CASH"))
    assert [i.trading_symbol for i in u.stocks] == ["RELIANCE"]
    assert [i.trading_symbol for i in u.indices] == ["NIFTY"]
    assert set(u.rejected) == {"SMECO", "NIFTY", "NOPE", "RELIANCE"}


def test_resolve_universe_respects_max_size(master):
    adapter = GrowwAdapter(master)
    config = {"symbols": ["RELIANCE", "SMECO"], "filters": {"max_universe_size": 1}}
    u = resolve_universe(config, lambda s, e: adapter.resolve_instrument(s, e, "CASH"))
    assert len(u.stocks) == 1 and "SMECO" in u.rejected
