"""state.json schema — M6 (spec §24-25, DECISIONS #14).

Incompatible changes increment SCHEMA_VERSION and are logged in
DECISIONS.md. Readers reject versions they do not know.
"""

from __future__ import annotations

from dataclasses import fields

from src.recommendation.models import MODES, Recommendation

SCHEMA_VERSION = 5
TOP_KEYS = ("schema_version", "as_of", "generated_at", "source", "demo", "data_age_seconds",
            "market", "modes", "excluded", "in_play_count", "universe_count", "errors",
            "feed")                     # v4 (M7): live feed status block
FEED_STATUSES = ("LIVE", "STALE", "DOWN", "OFF")
REC_KEYS = tuple(f.name for f in fields(Recommendation))


def validate_state(state) -> list[str]:
    """Problems found; empty list = valid."""
    if not isinstance(state, dict):
        return ["state is not an object"]
    if state.get("schema_version") != SCHEMA_VERSION:
        return [f"unsupported schema_version {state.get('schema_version')!r} "
                f"(expected {SCHEMA_VERSION})"]
    problems = [f"missing key {k}" for k in TOP_KEYS if k not in state]
    status = (state.get("feed") or {}).get("status")
    if "feed" in state and status not in FEED_STATUSES:
        problems.append(f"feed.status {status!r} not in {FEED_STATUSES}")
    for mode in MODES:
        recs = (state.get("modes") or {}).get(mode)
        if not isinstance(recs, list):
            problems.append(f"modes.{mode} missing")
            continue
        for r in recs:
            missing = [k for k in REC_KEYS if k not in r]
            if missing:
                problems.append(f"{mode} {r.get('symbol')}: missing {missing}")
    return problems
