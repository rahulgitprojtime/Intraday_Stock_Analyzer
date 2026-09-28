"""Per-stock news verdict — M9 (DECISIONS #19).

Classifies each recent headline (headline_rules), merges the same story
reported by several outlets (word overlap vs the shorter headline >= 60%,
same direction), weights each story by its number of outlets, and gives a
verdict: POSITIVE (upward catalyst) / NEGATIVE / MIXED / NEUTRAL /
NO_RELEVANT_INFORMATION. Every listed item is a real headline with its
link; nothing is summarised or invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.qualitative.headline_rules import DOWN, UP, NewsRules, _norm, classify_headline
from src.qualitative.news_source import NewsItem

MERGE_OVERLAP = 0.6
STOPWORDS = frozenset("a an and as at by for from in into of on or the to with after amid".split())
DIRECTION_ORDER = {DOWN: 0, UP: 1, "NEUTRAL": 2}      # show downside first: long-only caution


def _tokens(title: str) -> set[str]:
    return {w for w in _norm(title).split() if w not in STOPWORDS}


def _same_story(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= MERGE_OVERLAP


def aggregate_news(items: list[NewsItem], symbol: str, rules: NewsRules, now: datetime,
                   lookback_hours: float, max_items: int) -> dict:
    since = now - timedelta(hours=lookback_hours)
    counts = {"catalyst": 0, "roundup": 0, "speculation": 0}
    stories: list[dict] = []
    for it in sorted(items, key=lambda i: i.published_at, reverse=True):
        if not since <= it.published_at <= now:
            continue
        r = classify_headline(it.title, it.source, symbol, rules)
        if not r.relevant:
            continue
        if r.kind != "catalyst":
            counts[r.kind] += 1
            continue
        toks = _tokens(it.title)
        match = next((s for s in stories
                      if s["direction"] == r.direction and _same_story(s["_tokens"], toks)), None)
        if match:
            if it.source not in match["_sources"]:
                match["_sources"].add(it.source)
                match["outlets"] += 1
            continue
        counts["catalyst"] += 1
        stories.append({"title": it.title, "source": it.source, "outlets": 1,
                        "published_at": it.published_at.isoformat(timespec="minutes"),
                        "link": it.link, "direction": r.direction, "phrase": r.phrase,
                        "reason": r.reason, "_tokens": toks, "_sources": {it.source}})
    up = sum(s["outlets"] for s in stories if s["direction"] == UP)
    down = sum(s["outlets"] for s in stories if s["direction"] == DOWN)
    if up > down:
        verdict = "POSITIVE"
    elif down > up:
        verdict = "NEGATIVE"
    elif up:
        verdict = "MIXED"
    elif stories:
        verdict = "NEUTRAL"
    else:
        verdict = "NO_RELEVANT_INFORMATION"
    stories.sort(key=lambda s: (DIRECTION_ORDER[s["direction"]], s["published_at"]), reverse=False)
    shown = [{k: v for k, v in s.items() if not k.startswith("_")} for s in stories[:max_items]]
    return {"status": "available", "verdict": verdict, "items": shown, "up_weight": up,
            "down_weight": down, "counts": counts, "lookback_hours": lookback_hours,
            "checked_at": now.isoformat(timespec="seconds")}
