"""News service — M9 (DECISIONS #19).

Staggered fetching so the per-minute ranking never waits on news: each
worker tick fetches up to `fetch_per_tick` stocks (never-fetched first,
then oldest), each re-fetched about every `refresh_minutes`. Raw items are
cached and re-aggregated at read time so the look-back window moves with
the clock. A failed fetch keeps the last good result until
`max_age_minutes`, then reports UNAVAILABLE. Not yet fetched → PENDING;
a stock with no aliases → None. Both show as NOT_CHECKED. Read-only;
nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.qualitative.headline_rules import NewsRules
from src.qualitative.news_check import aggregate_news


@dataclass(frozen=True)
class NewsConfig:
    lookback_hours: float = 18
    refresh_minutes: float = 10
    max_age_minutes: float = 30
    fetch_per_tick: int = 3
    timeout_seconds: float = 10
    max_items_shown: int = 5

    @classmethod
    def from_dict(cls, d: dict) -> NewsConfig:
        return cls(**{k: type(getattr(cls, k))(d[k]) for k in cls.__dataclass_fields__
                      if k in d})


def unavailable(reason: str) -> dict:
    return {"status": "unavailable", "verdict": "UNAVAILABLE", "items": [], "reason": reason}


class NewsService:
    def __init__(self, source, rules: NewsRules, cfg: NewsConfig, aliases: dict,
                 symbols: list[str]) -> None:
        self.source, self.rules, self.cfg = source, rules, cfg
        self._aliases = {s: aliases[s] for s in symbols if aliases.get(s)}
        self._items: dict[str, list] = {}
        self._fetched_at: dict[str, datetime] = {}      # last successful fetch
        self._attempted_at: dict[str, datetime] = {}    # last attempt (success or not)
        self._failed: set[str] = set()

    def _due(self, now: datetime) -> list[str]:
        refresh = self.cfg.refresh_minutes * 60
        due = [s for s in self._aliases
               if s not in self._attempted_at
               or (now - self._attempted_at[s]).total_seconds() >= refresh]
        return sorted(due, key=lambda s: (s in self._attempted_at,
                                          self._attempted_at.get(s, now)))

    def tick(self, now: datetime) -> list[str]:
        """Fetch the next few due stocks; returns error strings (never raises)."""
        errors = []
        for sym in self._due(now)[: self.cfg.fetch_per_tick]:
            self._attempted_at[sym] = now
            try:
                self._items[sym] = self.source.fetch(self._aliases[sym])
                self._fetched_at[sym] = now
                self._failed.discard(sym)
            except Exception as exc:        # network, 429, bad XML: keep last good
                self._failed.add(sym)
                errors.append(f"news {sym}: {type(exc).__name__}: {exc}")
        return errors

    def result(self, symbol: str, now: datetime) -> dict | None:
        if symbol not in self._aliases:
            return None
        if symbol not in self._attempted_at:
            return {"status": "unavailable", "verdict": "PENDING", "items": [],
                    "reason": "first news fetch pending"}
        fetched = self._fetched_at.get(symbol)
        if fetched is None:
            return unavailable("news fetch failed")
        age = (now - fetched).total_seconds() / 60
        if age > self.cfg.max_age_minutes:
            return unavailable(f"news older than {self.cfg.max_age_minutes:g} min "
                               "(last fetch failed or pending)")
        out = aggregate_news(self._items[symbol], symbol, self.rules, now,
                             self.cfg.lookback_hours, self.cfg.max_items_shown)
        return out | {"source": self.source.name, "age_minutes": round(age, 1),
                      "fetched_at": fetched.isoformat(timespec="seconds")}
