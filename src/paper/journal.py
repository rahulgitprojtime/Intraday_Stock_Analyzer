"""Trade journal — M10 (DECISIONS #20).

Append-only JSONL event log: ENTRY (full entry-time snapshot), EXIT
(outcome fields only), MISSED (qualifying signal not taken, with reason).
There is no update or delete API: an entry can be recorded once, an exit
once, and an exit can never overwrite an entry field.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from src.utils.config import CONFIG_DIR, load_yaml

IMMUTABLE_ENTRY_FIELDS = frozenset({
    "trade_id", "date", "symbol", "mode", "direction", "signal_at", "entry_timestamp",
    "entry_price", "stop_loss", "target", "stop_method", "initial_risk", "risk_reward",
    "quantity", "recommendation_score", "category", "rank", "best_setup", "setup_state",
    "setup_family_confluence", "in_play_score", "market_context", "sector_context",
    "microstructure", "qualitative_context", "prerequisites", "entry_reason",
    "strategy_version", "config_hash", "git_commit", "data_source", "replay_or_live",
})
VERSIONED_CONFIGS = ("strategy.yaml", "paper.yaml", "sectors.yaml", "news.yaml",
                     "universe.yaml")


def trade_id(run_id: str, day: str, symbol: str, mode: str, signal_at: str,
             strategy_version: str) -> str:
    key = "|".join((run_id, day, symbol, mode, signal_at, strategy_version))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def version_info() -> dict:
    h = hashlib.sha256()
    for name in VERSIONED_CONFIGS:
        h.update((CONFIG_DIR / name).read_bytes())
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, cwd=CONFIG_DIR.parent, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    return {"strategy_version": str(load_yaml("strategy.yaml")["strategy_version"]),
            "config_hash": h.hexdigest()[:12], "git_commit": commit or "unknown"}


class Journal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        self._exits: dict[str, dict] = {}
        self._missed: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                self._apply(json.loads(line))

    def _apply(self, ev: dict) -> None:
        body = {k: v for k, v in ev.items() if k != "event"}
        if ev["event"] == "ENTRY":
            self._entries[body["trade_id"]] = body
        elif ev["event"] == "EXIT":
            self._exits[body["trade_id"]] = body
        else:
            self._missed.append(body)

    def _append(self, ev: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
        self._apply(ev)

    def record_entry(self, entry: dict) -> None:
        if entry["trade_id"] in self._entries:
            raise ValueError(f"trade {entry['trade_id']} already recorded (entries are immutable)")
        self._append({"event": "ENTRY"} | entry)

    def record_exit(self, tid: str, outcome: dict) -> None:
        if tid not in self._entries:
            raise ValueError(f"unknown trade {tid}")
        if tid in self._exits:
            raise ValueError(f"trade {tid} already exited")
        clash = IMMUTABLE_ENTRY_FIELDS & set(outcome) - {"trade_id"}
        if clash:
            raise ValueError(f"exit cannot change immutable entry fields: {sorted(clash)}")
        self._append({"event": "EXIT", "trade_id": tid} | outcome)

    def record_missed(self, signal: dict) -> None:
        self._append({"event": "MISSED"} | signal)

    def trades(self) -> list[dict]:
        out = []
        for tid, e in self._entries.items():
            x = self._exits.get(tid, {})
            out.append(e | {"exit_timestamp": None, "exit_price": None, "exit_reason": None} | x)
        return out

    def missed(self) -> list[dict]:
        return list(self._missed)
