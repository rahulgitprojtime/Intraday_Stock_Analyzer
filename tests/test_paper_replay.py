import json
from datetime import datetime, time
from pathlib import Path

from scripts.paper_replay import run_paper_day
from src.data.models import Candle
from src.paper.policy import PaperConfig
from src.storage.candle_cache import IntradayCandleCache
from src.utils.config import load_yaml
from tests.fakes.replay_fixture import REPLAY_DAY, write_replay_fixture

RAW = load_yaml("paper.yaml")
# Permissive entry so the tiny synthetic fixture produces trades to inspect.
LOOSE = PaperConfig.from_dict(RAW | {"entry": RAW["entry"] | {
    "minimum_category": "NEUTRAL", "minimum_score": 0, "require_triggered_setup": False}})
POISON_FROM = time(9, 30)


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def run(root, out, cfg=LOOSE):
    return run_paper_day(root, REPLAY_DAY, out, cfg, run_id="test")


def test_replay_run_writes_journal_and_report(tmp_path):
    write_replay_fixture(tmp_path / "r")
    res = run(tmp_path / "r", tmp_path / "out")
    assert res["journal"].exists() and (res["report_dir"] / "daily_report.md").exists()
    events = lines(res["journal"])
    assert any(e["event"] == "ENTRY" for e in events)
    for e in (e for e in events if e["event"] == "ENTRY"):
        assert e["replay_or_live"] == "replay" and e["strategy_version"]
        assert e["qualitative_context"] == {"status": "unavailable"}     # no historical news
        assert e["entry_timestamp"] >= e["signal_at"]                    # fill after signal


def test_same_input_same_output(tmp_path):
    write_replay_fixture(tmp_path / "r")
    a = run(tmp_path / "r", tmp_path / "a")["journal"].read_bytes()
    b = run(tmp_path / "r", tmp_path / "b")["journal"].read_bytes()
    assert a == b


def poison_future(root: Path) -> None:
    """Multiply every bar at/after POISON_FROM on the replay day by 10."""
    cache = IntradayCandleCache(root)
    for f in (root / REPLAY_DAY.isoformat()).glob("*.csv"):
        from src.data.models import Exchange, Instrument, Segment
        inst = Instrument(f.stem, Exchange.NSE, Segment.CASH, is_index=f.stem == "NIFTY")
        bars = cache.load(inst, REPLAY_DAY)
        cache.save(inst, REPLAY_DAY, [
            b if b.timestamp.time() < POISON_FROM else
            Candle(b.instrument, 1, b.timestamp, b.open * 10, b.high * 10, b.low * 10,
                   b.close * 10, b.volume * 10) for b in bars])


def test_future_bars_cannot_change_earlier_decisions_or_fills(tmp_path):
    write_replay_fixture(tmp_path / "clean")
    write_replay_fixture(tmp_path / "poison")
    poison_future(tmp_path / "poison")
    clean = lines(run(tmp_path / "clean", tmp_path / "c")["journal"])
    dirty = lines(run(tmp_path / "poison", tmp_path / "d")["journal"])
    cutoff = datetime.combine(REPLAY_DAY, POISON_FROM).isoformat()

    def before(events):
        # entries/missed decided and filled strictly before the poisoned bars
        return [{k: v for k, v in e.items() if k != "git_commit"} for e in events
                if e["event"] in ("ENTRY", "MISSED") and e["signal_at"] < cutoff
                and e.get("entry_timestamp", e["signal_at"]) < cutoff]

    assert before(clean), "fixture must produce early decisions for this test to mean anything"
    assert before(clean) == before(dirty)
