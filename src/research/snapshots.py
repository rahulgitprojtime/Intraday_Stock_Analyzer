"""Candidate snapshots for research — M14 (DECISIONS #24).

Every scored stock is recorded every `panel_minutes` (the comparison group
needs the low scorers too), plus an "event" row whenever a stock moves up
into a better category at WATCH or above. Rows are flat: one column per
group score, sub-signal, setup state, sector/news/microstructure value and
time rule, so each research question is a filter or a bucket on one file.

No prices are stored (#11): the outcome labeller takes the entry from the
open of the snapshot minute's bar after the close.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path

CATEGORY_ORDER = ("AVOID", "NEUTRAL", "WATCH", "CANDIDATE", "STRONG_CANDIDATE")
SESSION = (time(9, 15), time(15, 30))


def _level(category: str) -> int:
    return CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else -1


def snapshot_row(rec: dict, as_of: str, trigger: str, meta: dict) -> dict:
    q = rec.get("quantitative") or {}
    setup = rec.get("setup") or {}
    sector = rec.get("sector_context") or {}
    news = rec.get("qualitative") or {}
    market = rec.get("market_context") or {}
    micro = q.get("microstructure") or {}
    adj = rec.get("adjustments") or []
    row = {
        "as_of": as_of, "day": as_of[:10], "symbol": rec["symbol"], "mode": rec["mode"],
        "trigger": trigger, **meta,
        "score": rec["score"], "base_score": rec["score"] - sum(a["points"] for a in adj),
        "category": rec["category"], "rank": rec.get("rank"), "profile": rec.get("profile"),
        "adjustments": [a["name"] for a in adj],
        "lunch_penalty": any(a["name"] == "lunch_lull" for a in adj),
        "is_in_play": q.get("is_in_play"),
        "setup_best": setup.get("best"), "setup_best_state": setup.get("best_state"),
        "confluence_count": setup.get("confluence_count"),
        "confluence_bonus": setup.get("confluence_bonus"),
        "triggered": [s["name"] for s in setup.get("signals", []) if s["state"] == "TRIGGERED"],
        "sector": sector.get("sector"), "sector_index": sector.get("index"),
        "sector_verdict": sector.get("verdict") if sector.get("status") == "available" else "UNAVAILABLE",
        "sector_rs": sector.get("sector_relative_strength"),
        "stock_vs_sector": sector.get("stock_vs_sector"),
        "news_verdict": news.get("verdict") if news.get("status") == "available" else "NOT_CHECKED",
        "spread_pct": micro.get("spread_pct"), "imbalance": micro.get("imbalance"),
        "tick_velocity": micro.get("tick_velocity"), "micro_score": micro.get("micro_score"),
        "market_score": market.get("score"), "nifty_change_pct": market.get("nifty_change_pct"),
        "data_quality": (rec.get("data_quality") or {}).get("status"),
    }
    for c in rec.get("components", []):
        row[f"g_{c['name']}"] = c["value"] if c["status"] == "available" else None
    for g, body in (q.get("groups") or {}).items():
        for part, v in (body.get("parts") or {}).items():
            row[f"p_{g}_{part}"] = v
        for k, v in (body.get("raw") or {}).items():
            row[f"raw_{g}_{k}"] = v
    for k, v in (q.get("in_play_features") or {}).items():
        row[k if k != "rs_pct" else "rs_vs_nifty_pct"] = v
    for s in setup.get("signals", []):
        row[f"setup_{s['name']}"] = s["state"]
    return row


class SnapshotRecorder:
    def __init__(self, out_dir: str | Path, meta: dict, panel_minutes: int = 5) -> None:
        self.out_dir, self.meta, self.panel_minutes = Path(out_dir), dict(meta), panel_minutes
        self._last: dict[tuple[str, str], str] = {}

    def record(self, state: dict) -> int:
        as_of = state["as_of"]
        t = datetime.fromisoformat(as_of)
        if not SESSION[0] <= t.time() <= SESSION[1]:
            return 0
        panel = t.minute % self.panel_minutes == 0
        rows = []
        for recs in state.get("modes", {}).values():
            for r in recs:
                key = (r["symbol"], r["mode"])
                prev = self._last.get(key)
                self._last[key] = r["category"]
                if panel:
                    rows.append(snapshot_row(r, as_of, "panel", self.meta))
                if (prev is not None and _level(r["category"]) > _level(prev)
                        and _level(r["category"]) >= _level("WATCH")):
                    rows.append(snapshot_row(r, as_of, "event", self.meta) | {"prev_category": prev})
        if rows:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with (self.out_dir / f"{t.date().isoformat()}.jsonl").open("a", encoding="utf-8") as f:
                f.writelines(json.dumps(x) + "\n" for x in rows)
        return len(rows)
