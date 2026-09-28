"""Headline-context rules — M9 (DECISIONS #19).

Deterministic classification of one real headline for one stock: is it
about the stock, is it a single-stock catalyst (not a roundup, price page
or speculation), and does it point up or down? The direction phrase
nearest the stock's name (within `subject_window_words`) decides; a
negation just before it neutralises it. No LLM, no invented text: the
input is a headline exactly as published, the output cites the phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UP, DOWN, NEUTRAL = "UP", "DOWN", "NEUTRAL"
NEGATION_WINDOW = 5            # words before a phrase that can negate it


CLAUSE = ";"                   # clause separator token kept by _norm (from ";" and "|")


def _norm(text: str, keep_case: bool = False) -> str:
    """Lowercase (unless keep_case); punctuation → space (keep & and %);
    ";" and "|" become a standalone ";" clause token; single spaces."""
    text = (text if keep_case else text.lower()).replace("’", "'")
    text = re.sub(r"[;|]", f" {CLAUSE} ", text)
    text = re.sub(r"[^\w&%;\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pattern(phrase: str, keep_case: bool = False) -> re.Pattern:
    """Whole-word phrase; `*` matches a gap of 0-4 words ("cuts * target"
    catches "Citi cuts TCS target")."""
    parts = [re.escape(_norm(p, keep_case)) for p in phrase.split("*")]
    body = r"(?:\s+\S+){0,4}\s+".join(p.strip().replace(r"\ ", r"\s+") for p in parts)
    return re.compile(r"(?<![\w&])" + body + r"(?![\w&])")


def _word_index(text: str, char_pos: int) -> int:
    return text.count(" ", 0, char_pos)


def _clause(text: str, word: int) -> int:
    return text.split(" ")[:word].count(CLAUSE)


@dataclass(frozen=True)
class NewsRules:
    aliases: dict                  # symbol -> [compiled alias patterns]
    cased: dict                    # symbol -> [case-sensitive alias patterns] (common words)
    exclude: dict                  # symbol -> [look-alike company patterns]
    skip_sources: frozenset
    skip_title: tuple
    roundup_min_companies: int
    roundup: tuple
    speculation: tuple
    negations: tuple
    upward: tuple
    downward: tuple
    window: int

    @classmethod
    def from_dict(cls, d: dict) -> NewsRules:
        pats = lambda xs: tuple(_pattern(x) for x in xs or [])  # noqa: E731
        cased = set(d.get("case_sensitive_aliases") or [])
        return cls(
            aliases={sym: pats([n for n in names if n not in cased])
                     for sym, names in (d.get("aliases") or {}).items()},
            cased={sym: tuple(_pattern(n, keep_case=True) for n in names if n in cased)
                   for sym, names in (d.get("aliases") or {}).items()},
            exclude={sym: pats(names) for sym, names in (d.get("exclude") or {}).items()},
            skip_sources=frozenset(s.lower() for s in d.get("skip_sources") or []),
            skip_title=pats(d.get("skip_title_phrases")),
            roundup_min_companies=int(d.get("roundup_min_companies", 3)),
            roundup=pats(d.get("roundup_phrases")),
            speculation=pats(d.get("speculation_phrases")),
            negations=pats(d.get("negations")),
            upward=tuple(zip(pats(d.get("upward")), d.get("upward") or [])),
            downward=tuple(zip(pats(d.get("downward")), d.get("downward") or [])),
            window=int(d.get("subject_window_words", 8)),
        )


    def with_aliases(self, extra: dict[str, list[str]]) -> NewsRules:
        """Rules that also recognise new universe members (M11); existing
        symbols keep their configured aliases."""
        from dataclasses import replace
        added = {sym: tuple(_pattern(n) for n in names)
                 for sym, names in extra.items() if sym not in self.aliases and names}
        return replace(self, aliases=self.aliases | added)


@dataclass(frozen=True)
class HeadlineResult:
    relevant: bool
    kind: str              # catalyst | roundup | speculation | irrelevant | skipped
    direction: str         # UP | DOWN | NEUTRAL
    phrase: str | None     # the direction phrase that decided it
    reason: str


def _positions(patterns, text: str) -> list[int]:
    return [_word_index(text, m.start()) for p in patterns for m in p.finditer(text)]


def classify_headline(title: str, source: str, symbol: str, rules: NewsRules) -> HeadlineResult:
    text = _norm(title)
    if (source or "").strip().lower() in rules.skip_sources or \
            any(p.search(text) for p in rules.skip_title):
        return HeadlineResult(False, "skipped", NEUTRAL, None, "price/quote page, not news")
    blank = lambda m: " ".join("_" * len(w) for w in m.group().split())  # noqa: E731
    named, cased = text, _norm(title, keep_case=True)
    for p in rules.exclude.get(symbol, ()):       # blank look-alikes, keep word positions
        named = p.sub(blank, named)
        cased = re.compile(p.pattern, re.IGNORECASE).sub(blank, cased)
    own = _positions(rules.aliases.get(symbol, ()), named) +         _positions(rules.cased.get(symbol, ()), cased)
    if not own:
        return HeadlineResult(False, "irrelevant", NEUTRAL, None, "stock not named in headline")
    cased_text = _norm(title, keep_case=True)
    companies = sum(1 for sym in rules.aliases
                    if any(p.search(text) for p in rules.aliases[sym])
                    or any(p.search(cased_text) for p in rules.cased.get(sym, ())))
    if companies >= rules.roundup_min_companies or any(p.search(text) for p in rules.roundup):
        return HeadlineResult(True, "roundup", NEUTRAL, None, "market roundup, not stock-specific")
    if title.rstrip().endswith("?") or any(p.search(text) for p in rules.speculation):
        return HeadlineResult(True, "speculation", NEUTRAL, None, "question/opinion headline")
    candidates = []
    for direction, phrases in ((UP, rules.upward), (DOWN, rules.downward)):
        for pat, phrase in phrases:
            for m in pat.finditer(text):
                at = _word_index(text, m.start())
                same = [o for o in own if _clause(text, o) == _clause(text, at)]
                if not same:
                    continue                      # phrase is about another clause's subject
                dist = min(abs(at - o) for o in same)
                if dist <= rules.window:
                    candidates.append((dist, at, -(m.end() - m.start()), "*" in phrase,
                                       direction, phrase))
    if not candidates:
        return HeadlineResult(True, "catalyst", NEUTRAL, None, "no direction phrase")
    # nearest to the stock; at the same spot the longest matched text wins,
    # then an exact phrase over a gapped one
    *_, direction, phrase = min(candidates)
    negated = [n for n in _positions(rules.negations, text) if 0 < at - n <= NEGATION_WINDOW]
    if negated:
        return HeadlineResult(True, "catalyst", NEUTRAL, phrase, f"negated: '{phrase}'")
    return HeadlineResult(True, "catalyst", direction, phrase, f"'{phrase}'")
