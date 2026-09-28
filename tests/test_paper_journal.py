import json

import pytest

from src.paper.journal import IMMUTABLE_ENTRY_FIELDS, Journal, trade_id, version_info


def entry(tid="t1", **kw):
    return {"trade_id": tid, "symbol": "AAA", "entry_price": 100.0, "stop_loss": 99.0,
            "target": 101.5, "recommendation_score": 70.0} | kw


def test_entry_exit_merge_and_reload(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.record_entry(entry())
    j.record_missed({"symbol": "BBB", "reason": "max open positions (3)"})
    assert j.trades()[0]["exit_reason"] is None                      # open
    j.record_exit("t1", {"exit_price": 101.5, "exit_reason": "TARGET"})
    again = Journal(tmp_path / "journal.jsonl")                       # survives reload
    t = again.trades()[0]
    assert (t["entry_price"], t["exit_price"], t["exit_reason"]) == (100.0, 101.5, "TARGET")
    assert again.missed() == [{"symbol": "BBB", "reason": "max open positions (3)"}]


def test_entries_are_immutable(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.record_entry(entry())
    with pytest.raises(ValueError, match="already recorded"):
        j.record_entry(entry(entry_price=90.0))
    j.record_exit("t1", {"exit_price": 99.0, "exit_reason": "STOP_LOSS"})
    with pytest.raises(ValueError, match="already exited"):
        j.record_exit("t1", {"exit_price": 105.0, "exit_reason": "TARGET"})
    j.record_entry(entry("t2"))
    with pytest.raises(ValueError, match="immutable"):
        j.record_exit("t2", {"exit_price": 99.0, "exit_reason": "STOP_LOSS", "entry_price": 50})
    with pytest.raises(ValueError, match="unknown trade"):
        j.record_exit("nope", {"exit_price": 1.0, "exit_reason": "TARGET"})
    lines = (tmp_path / "journal.jsonl").read_text().splitlines()
    assert [json.loads(x)["event"] for x in lines] == ["ENTRY", "EXIT", "ENTRY"]  # append-only
    assert "entry_price" in IMMUTABLE_ENTRY_FIELDS and "stop_loss" in IMMUTABLE_ENTRY_FIELDS


def test_trade_id_stable_and_unique():
    a = trade_id("run", "2026-09-25", "AAA", "DAY", "10:15", "v1")
    assert a == trade_id("run", "2026-09-25", "AAA", "DAY", "10:15", "v1")
    assert a != trade_id("run", "2026-09-25", "AAA", "DAY", "10:16", "v1")
    assert a != trade_id("run", "2026-09-25", "AAA", "DAY", "10:15", "v2")


def test_version_info_records_strategy_config_and_commit():
    v = version_info()
    assert v["strategy_version"] == "2026-09-29.m14"
    assert len(v["config_hash"]) == 12 and v["git_commit"]
