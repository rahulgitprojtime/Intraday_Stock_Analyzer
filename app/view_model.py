"""Dashboard view model — M6 (spec §19).

Stdlib only, so it is testable without Streamlit. Reads `state.json`;
never imports `src/broker` or `src/data`. Scores rank candidates; no text
here may claim a probability of profit (spec §14).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from src.recommendation.schema import validate_state

DISCLAIMER = ("Scores rank candidates; not a probability of profit. Not investment advice. "
              "No orders are placed.")
STALE_AFTER_SECONDS = 120
RANKABLE_CATEGORIES = ("STRONG_CANDIDATE", "CANDIDATE", "WATCH", "NEUTRAL")
DEFAULT_CATEGORIES = ("STRONG_CANDIDATE", "CANDIDATE", "WATCH")


def load_state(path: str | Path) -> tuple[dict | None, str | None]:
    """(state, None) or (None, error message for a banner)."""
    path = Path(path)
    if not path.exists():
        return None, "No state file yet - start the worker."
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"State file unreadable: {exc}"
    problems = validate_state(state)
    if problems:
        return None, "State file invalid: " + "; ".join(problems[:3])
    return state, None


def banners(state: dict, now: datetime) -> list[tuple[str, str]]:
    """(streamlit level, text) pairs; the disclaimer is always last."""
    out = []
    if state.get("demo"):
        out.append(("warning", "DEMO data - synthetic candles, not market data and not "
                               "strategy evidence."))
    age = (now - datetime.fromisoformat(state["generated_at"])).total_seconds()
    if age > STALE_AFTER_SECONDS:
        out.append(("error", f"State is stale: last update {int(age)} s ago. "
                             "Is the worker running?"))
    feed = state.get("feed") or {}
    if feed.get("status") in ("STALE", "DOWN"):
        out.append(("warning", f"Live feed {feed['status']}: spread check and Scalp "
                               "microstructure paused; candles still refresh."))
    if state.get("excluded"):
        out.append(("info", f"{len(state['excluded'])} symbol(s) excluded "
                            "(stale data, liquidity, or errors)."))
    out.append(("caption", DISCLAIMER))
    return out


def select(recs: Sequence[dict], categories: Iterable[str], min_score: float,
           top_n: int) -> list[dict]:
    """Top-N from rankable candidates only; AVOID never enters, even if asked."""
    cats = set(categories)
    chosen = [r for r in recs if r["eligible_for_top_n"] and r["category"] in cats
              and r["score"] >= min_score]
    return sorted(chosen, key=lambda r: r["rank"])[:top_n]


def avoided(recs: Sequence[dict]) -> list[dict]:
    """AVOID candidates (scored, not rankable), highest raw score first."""
    return sorted((r for r in recs if not r["eligible_for_top_n"]),
                  key=lambda r: (-r["score"], r["symbol"]))


def table_rows(recs: Sequence[dict]) -> list[dict]:
    rows = []
    for r in recs:
        q, m = r["quantitative"], r["market_context"]
        rvol = q["in_play_features"]["rvol"]
        rows.append({
            "Rank": r["rank"],
            "Symbol": r["symbol"],
            "Category": r["category"],
            "Score": round(r["score"], 1),
            "Best Setup": r["setup"]["best"] or "-",
            "Setup State": r["setup"]["best_state"],
            "RVOL": "-" if rvol is None else f"{rvol:.1f}x",
            "In Play": "Yes" if q["is_in_play"] else "No",
            "Sector": _sector_cell(r["sector_context"]),
            "Market Context": "unavailable" if m["status"] != "available" else f"{m['score']:.0f}",
            "Prerequisites": r["prerequisites_summary"],
        })
    return rows


def _sector_cell(sc: dict) -> str:
    if sc["status"] != "available":
        return "unavailable"
    return f"{sc['sector']} {sc['verdict'].lower()}"


CHECK_MARK = {"PASS": "✓", "WARN": "~", "FAIL": "✗", "NA": "–", "NOT_CHECKED": "–"}
CHECK_LABEL = {"technicals": "Technicals", "in_play": "In play", "liquidity": "Liquidity",
               "sector": "Sector", "market": "Market", "news": "News"}


def checklist_lines(rec: dict) -> list[str]:
    """What was checked before listing this stock, in funnel order (M8)."""
    return [f"{CHECK_MARK[c['status']]} {CHECK_LABEL.get(c['check'], c['check'])}: {c['detail']}"
            for c in rec["prerequisites"]]
