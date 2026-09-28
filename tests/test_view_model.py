import json
from datetime import datetime, time, timedelta

from app.view_model import DISCLAIMER, avoided, banners, load_state, select, table_rows
from src.app.worker import write_state
from tests.test_worker import GEN, context, run_tick
from tests.fakes.replay_fixture import REPLAY_DAY

BANNED_PHRASES = ("chance of profit", "guaranteed", "probability of winning", "expected return")


def real_state(tmp_path):
    return run_tick(context(tmp_path / "r"), datetime.combine(REPLAY_DAY, time(9, 30)), GEN)


def test_load_missing_corrupt_and_unknown_version(tmp_path):
    assert load_state(tmp_path / "none.json")[0] is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    state, err = load_state(bad)
    assert state is None and "unreadable" in err
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema_version": 2}))
    state, err = load_state(old)
    assert state is None and "schema_version" in err


def test_load_valid_state(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, real_state(tmp_path))
    state, err = load_state(path)
    assert err is None and state["schema_version"] == 4


def test_banners_demo_stale_and_disclaimer_last(tmp_path):
    state = real_state(tmp_path) | {"demo": True}
    fresh = banners(state, GEN + timedelta(seconds=30))
    assert fresh[0][0] == "warning" and "DEMO" in fresh[0][1]
    assert fresh[-1] == ("caption", DISCLAIMER)
    assert not any("stale" in text for _, text in fresh)
    stale = banners(state, GEN + timedelta(minutes=10))
    assert any(level == "error" and "stale" in text for level, text in stale)
    assert not any("feed" in text.lower() for _, text in fresh)         # replay: OFF
    down = banners(state | {"feed": state["feed"] | {"status": "DOWN"}}, GEN)
    assert any(level == "warning" and "Live feed DOWN" in text for level, text in down)
    all_text = " ".join(t for _, t in stale).lower()
    assert not [p for p in BANNED_PHRASES if p in all_text]


def rec(sym, rank, score, category):
    return {"symbol": sym, "rank": rank, "score": score, "category": category,
            "eligible_for_top_n": category != "AVOID"}


RECS = [rec("C", 2, 55, "WATCH"), rec("A", 1, 85, "STRONG_CANDIDATE"),
        rec("B", None, 95, "AVOID"), rec("D", 3, 40, "NEUTRAL")]


def test_select_filters_orders_and_limits():
    got = select(RECS, ("STRONG_CANDIDATE", "WATCH"), 0, 10)
    assert [r["symbol"] for r in got] == ["A", "C"]
    assert [r["symbol"] for r in select(RECS, ("WATCH", "STRONG_CANDIDATE"), 60, 10)] == ["A"]
    assert len(select(RECS, ("STRONG_CANDIDATE", "WATCH", "NEUTRAL"), 0, 2)) == 2
    assert select([], ("WATCH",), 0, 5) == []


def test_avoid_never_in_top_n_even_if_requested():
    got = select(RECS, ("STRONG_CANDIDATE", "WATCH", "NEUTRAL", "AVOID"), 0, 10)
    assert "B" not in [r["symbol"] for r in got]


def test_avoided_listed_separately_for_transparency():
    assert [r["symbol"] for r in avoided(RECS)] == ["B"]


def test_table_rows_columns_and_no_prices(tmp_path):
    recs = real_state(tmp_path)["modes"]["SCALP"]
    rows = table_rows(recs)
    assert list(rows[0]) == ["Rank", "Symbol", "Category", "Score", "Best Setup", "Setup State",
                             "RVOL", "In Play", "Market Context"]
    assert [row["Rank"] for row in rows] == [r["rank"] for r in recs]


def test_dashboard_never_imports_broker_or_data_layers():
    import re
    from pathlib import Path

    for path in Path("app").glob("*.py"):
        imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", path.read_text(), re.M)
        assert not [m for m in imports if m.startswith(("src.broker", "src.data"))], path
