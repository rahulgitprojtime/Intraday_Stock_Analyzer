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


def _norm(text: str) -> str:
    """Lowercase; punctuation → space (keep & and %), single spaces."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^\w&%\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pattern(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![\w&])" + re.escape(_norm(phrase)) + r"(?![\w&])")


def _word_index(text: str, char_pos: int) -> int:
    return text.count(" ", 0, char_pos)


@dataclass(frozen=True)
class NewsRules:
    aliases: dict                  # symbol -> [compiled alias patterns]
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
        return cls(
            aliases={sym: pats(names) for sym, names in (d.get("aliases") or {}).items()},
            skip_sources=frozenset(s.lower() for s in d.get("skip_sources") or []),
            skip_title=pats(d.get("skip_title_phrases")),
            roundup_min_companies=int(d.get("roundup_min_companies", 3)),
            roundup=pats(d.get("roundup_phrases")),
            speculation=pats(d.get("speculation_phrases")),
            negations=pats(d.get("negations")),
            upward=tuple((p, _norm(x)) for p, x in zip(pats(d.get("upward")), d.get("upward") or [])),
            downward=tuple((p, _norm(x)) for p, x in zip(pats(d.get("downward")),
                                                          d.get("downward") or [])),
            window=int(d.get("subject_window_words", 8)),
        )


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
    own = _positions(rules.aliases.get(symbol, ()), text)
    if not own:
        return HeadlineResult(False, "irrelevant", NEUTRAL, None, "stock not named in headline")
    companies = sum(1 for pats in rules.aliases.values() if any(p.search(text) for p in pats))
    if companies >= rules.roundup_min_companies or any(p.search(text) for p in rules.roundup):
        return HeadlineResult(True, "roundup", NEUTRAL, None, "market roundup, not stock-specific")
    if title.rstrip().endswith("?") or any(p.search(text) for p in rules.speculation):
        return HeadlineResult(True, "speculation", NEUTRAL, None, "question/opinion headline")
    candidates = []
    for direction, phrases in ((UP, rules.upward), (DOWN, rules.downward)):
        for pat, phrase in phrases:
            for m in pat.finditer(text):
                at = _word_index(text, m.start())
                dist = min(abs(at - o) for o in own)
                if dist <= rules.window:
                    candidates.append((dist, at, direction, phrase))
    if not candidates:
        return HeadlineResult(True, "catalyst", NEUTRAL, None, "no direction phrase")
    dist, at, direction, phrase = min(candidates, key=lambda c: (c[0], c[1]))
    negated = [n for n in _positions(rules.negations, text) if 0 < at - n <= NEGATION_WINDOW]
    if negated:
        return HeadlineResult(True, "catalyst", NEUTRAL, phrase, f"negated: '{phrase}'")
    return HeadlineResult(True, "catalyst", direction, phrase, f"'{phrase}'")
