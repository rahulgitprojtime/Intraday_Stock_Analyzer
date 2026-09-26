import json
from datetime import datetime, time

import pytest

from src.app.sources import ReplaySource
from src.app.worker import base_context, main, prepare, run_tick
from src.recommendation.schema import validate_state
from tests.fakes.replay_fixture import REPLAY_DAY, write_replay_fixture

AS_OF = datetime.combine(REPLAY_DAY, time(9, 30))
GEN = datetime(2026, 9, 25, 9, 30, 5)


def context(root, **fixture):
    write_replay_fixture(root, **fixture)
    src = ReplaySource(root, REPLAY_DAY)
    stocks, index = src.instruments()
    ctx = base_context(src, stocks, index, "replay", src.is_demo)
    prepare(ctx, REPLAY_DAY)
    return ctx


def test_tick_writes_schema_valid_ranked_state(tmp_path):
    state = run_tick(context(tmp_path), AS_OF, GEN)
    assert validate_state(state) == []
    assert state["schema_version"] == 3 and state["source"] == "replay"
    assert state["universe_count"] == 2 and state["errors"] == []
    for mode in ("SCALP", "DAY"):
        recs = state["modes"][mode]
        ranked = [r for r in recs if r["eligible_for_top_n"]]
        assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
        avoid = [r for r in recs if not r["eligible_for_top_n"]]
        assert all(r["rank"] is None and r["category"] == "AVOID" and r["exclusion_reasons"]
                   for r in avoid)                        # kept for transparency
        assert all(r["data_quality"]["market_data_timestamp"] < state["as_of"] for r in recs)
    json.dumps(state)                                    # JSON-serializable


def test_prep_failure_is_reported_not_fatal(tmp_path):
    ctx = context(tmp_path)
    original = ctx.source.prep

    def flaky(inst, day):
        if inst.trading_symbol == "AAA":
            raise RuntimeError("boom")
        return original(inst, day)

    ctx.source.prep = flaky
    ctx.errors.clear()
    prepare(ctx, REPLAY_DAY)
    state = run_tick(ctx, AS_OF, GEN)
    assert any("AAA" in e and "boom" in e for e in state["errors"])
    assert [x["symbol"] for x in state["excluded"]] == ["AAA"]
    assert [r["symbol"] for r in state["modes"]["DAY"]] == ["BBB"]


def test_missing_index_marks_market_unavailable(tmp_path):
    state = run_tick(context(tmp_path, with_index=False), AS_OF, GEN)
    assert state["market"]["status"] == "unavailable"
    rec = state["modes"]["SCALP"][0]
    assert "index" in rec["data_quality"]["missing_inputs"]


def test_all_excluded_is_still_valid_state(tmp_path):
    late = datetime.combine(REPLAY_DAY, time(10, 30))    # fixture bars end 09:44 → stale
    state = run_tick(context(tmp_path), late, GEN)
    assert validate_state(state) == []
    assert state["modes"] == {"SCALP": [], "DAY": []} and len(state["excluded"]) == 2


def test_validate_state_rejects_unknown_version():
    assert "schema_version" in validate_state({"schema_version": 2})[0]


def run_cli(root, out, ticks="3"):
    return main(["--replay", str(root), "--day", REPLAY_DAY.isoformat(), "--speed", "0",
                 "--ticks", ticks, "--out", str(out)])


def test_cli_replay_writes_state_and_is_deterministic(tmp_path):
    write_replay_fixture(tmp_path / "r")
    assert run_cli(tmp_path / "r", tmp_path / "a.json") == 0
    assert run_cli(tmp_path / "r", tmp_path / "b.json") == 0
    a = json.loads((tmp_path / "a.json").read_text())
    b = json.loads((tmp_path / "b.json").read_text())
    assert a["as_of"] == datetime.combine(REPLAY_DAY, time(9, 18)).isoformat()
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_cli_rejects_empty_replay_day(tmp_path):
    with pytest.raises(SystemExit):
        run_cli(tmp_path, tmp_path / "s.json")
