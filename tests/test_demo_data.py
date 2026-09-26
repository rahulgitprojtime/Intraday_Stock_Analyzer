import json
from datetime import date

from scripts.make_demo_data import generate_demo
from src.app.worker import main
from src.recommendation.schema import validate_state

DAY = date(2026, 9, 25)   # a Friday


def files(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_demo_data_is_deterministic_and_labeled(tmp_path):
    names = generate_demo(tmp_path / "a", DAY)
    generate_demo(tmp_path / "b", DAY)
    assert files(tmp_path / "a") == files(tmp_path / "b")
    assert "NIFTY" in names and len(names) == 11
    assert (tmp_path / "a" / "DEMO").exists()
    sessions = [p for p in (tmp_path / "a").iterdir() if p.is_dir()]
    assert len(sessions) == 21 and all(date.fromisoformat(p.name).weekday() < 5 for p in sessions)


def test_replay_on_demo_end_to_end(tmp_path):
    generate_demo(tmp_path / "demo", DAY)
    out = tmp_path / "state.json"
    assert main(["--replay", str(tmp_path / "demo"), "--day", DAY.isoformat(), "--speed", "0",
                 "--ticks", "30", "--out", str(out)]) == 0
    state = json.loads(out.read_text())
    assert validate_state(state) == [] and state["demo"] is True
    assert {"DEMO10": True} == {x["symbol"]: x["reason"].startswith("liquidity")
                                for x in state["excluded"] if x["symbol"] == "DEMO10"}
    assert state["modes"]["SCALP"], "liquid demo symbols should be ranked"
    for rec in state["modes"]["SCALP"] + state["modes"]["DAY"]:
        assert rec["data_quality"]["market_data_timestamp"] < state["as_of"]
