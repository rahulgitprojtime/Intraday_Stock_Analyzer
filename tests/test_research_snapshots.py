import json
from datetime import datetime

from src.research.snapshots import SnapshotRecorder, snapshot_row


def rec(symbol="AAA", mode="DAY", score=70.0, category="CANDIDATE", adjustments=(), **kw):
    r = {
        "symbol": symbol, "mode": mode, "score": score, "category": category, "rank": 1,
        "profile": "IN_PLAY_SETUP",
        "components": [{"name": "volume", "value": 80.0, "weight": 0.2, "status": "available"},
                       {"name": "news", "value": None, "weight": None, "status": "unavailable"}],
        "quantitative": {"is_in_play": True,
                         "in_play_features": {"rvol": 2.5, "gap_pct": 0.4},
                         "groups": {"volume": {"value": 80.0, "parts": {"rvol": 50.0,
                                                                        "acceleration": None},
                                               "raw": {"acceleration": 1.7}},
                                    "movement": {"value": 50.0, "parts": {},
                                                 "raw": {"day_change_pct": 2.4}}},
                         "microstructure": {"spread_pct": 0.05, "imbalance": 0.2,
                                            "tick_velocity": 1.4, "micro_score": 60.0}},
        "setup": {"best": "ORB15", "best_state": "TRIGGERED", "confluence_count": 2,
                  "confluence_bonus": 2.0,
                  "signals": [{"name": "ORB15", "state": "TRIGGERED"},
                              {"name": "PDH", "state": "NONE"}]},
        "market_context": {"status": "available", "score": 55.0, "nifty_change_pct": 0.2},
        "sector_context": {"status": "available", "sector": "AUTO", "index": "NIFTYAUTO",
                           "verdict": "CONFIRMED", "sector_relative_strength": 0.3,
                           "stock_vs_sector": 0.1},
        "qualitative": {"status": "available", "verdict": "POSITIVE"},
        "adjustments": list(adjustments),
        "data_quality": {"status": "OK"},
    }
    r.update(kw)
    return r


def state(as_of, recs):
    return {"as_of": as_of, "modes": {"SCALP": [r for r in recs if r["mode"] == "SCALP"],
                                      "DAY": [r for r in recs if r["mode"] == "DAY"]}}


def test_row_flattens_every_tested_signal_without_prices():
    lunch = {"name": "lunch_lull", "kind": "penalty", "points": -10.0, "reason": ""}
    row = snapshot_row(rec(score=60.0, adjustments=[lunch]), "2026-09-29T12:00:00", "panel",
                       {"strategy_version": "v"})
    assert row["score"] == 60.0 and row["base_score"] == 70.0 and row["lunch_penalty"] is True
    assert row["g_volume"] == 80.0 and row["g_news"] is None
    assert row["p_volume_rvol"] == 50.0 and row["p_volume_acceleration"] is None
    assert row["raw_movement_day_change_pct"] == 2.4 and row["raw_volume_acceleration"] == 1.7
    assert row["rvol"] == 2.5 and row["setup_ORB15"] == "TRIGGERED"
    assert row["triggered"] == ["ORB15"] and row["confluence_count"] == 2
    assert row["sector_verdict"] == "CONFIRMED" and row["sector_index"] == "NIFTYAUTO"
    assert row["news_verdict"] == "POSITIVE" and row["micro_score"] == 60.0
    assert row["nifty_change_pct"] == 0.2 and row["strategy_version"] == "v"
    assert not any("price" in k for k in row)            # no price levels (#11)


def test_panel_every_5_minutes_and_events_on_upgrade(tmp_path):
    r = SnapshotRecorder(tmp_path, {"strategy_version": "v"}, panel_minutes=5)
    r.record(state("2026-09-29T09:16:00", [rec(category="NEUTRAL")]))      # first sight: not >= WATCH
    r.record(state("2026-09-29T09:17:00", [rec(category="WATCH")]))        # upgrade -> event
    r.record(state("2026-09-29T09:18:00", [rec(category="WATCH")]))        # no change
    r.record(state("2026-09-29T09:19:00", [rec(category="NEUTRAL")]))      # downgrade: nothing
    r.record(state("2026-09-29T09:20:00", [rec(category="CANDIDATE")]))    # panel + event
    rows = [json.loads(x) for x in (tmp_path / "2026-09-29.jsonl").read_text().splitlines()]
    assert [(x["as_of"][11:16], x["trigger"]) for x in rows] == [
        ("09:17", "event"), ("09:20", "panel"), ("09:20", "event")]
    assert rows[2]["prev_category"] == "NEUTRAL"


def test_ignores_minutes_outside_the_session(tmp_path):
    r = SnapshotRecorder(tmp_path, {}, panel_minutes=5)
    r.record(state("2026-09-29T09:10:00", [rec()]))
    r.record(state("2026-09-29T15:35:00", [rec()]))
    assert not (tmp_path / "2026-09-29.jsonl").exists()
