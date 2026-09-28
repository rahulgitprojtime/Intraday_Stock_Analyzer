"""News sources — M9 (DECISIONS #19).

`NewsSource.fetch(aliases) -> list[NewsItem]`: real published headlines
with source, time and link. First implementation: Google News RSS search
(no key; headline only — the RSS description repeats the title, verified
2026-09-28). NSE/BSE announcement APIs return 403 to scripts and are not
used. A keyed API (Marketaux, Drishti) can implement the same interface.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

IST = timezone(timedelta(hours=5, minutes=30))   # no DST; avoids a tzdata dependency
RSS_URL = "https://news.google.com/rss/search?"
USER_AGENT = "Mozilla/5.0 (intraday-stock-analyzer; read-only news check)"


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published_at: datetime          # naive IST, like the rest of the app
    link: str


def search_query(aliases: list[str]) -> str:
    return " OR ".join(f'"{a}"' for a in aliases) + " when:1d"


def parse_rss(xml: bytes) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"not RSS: {exc}") from exc
    items = []
    for it in root.findall("./channel/item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue                                  # undated items can't be checked for recency
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        items.append(NewsItem(title, source, published.astimezone(IST).replace(tzinfo=None),
                              (it.findtext("link") or "").strip()))
    return items


def _urlopen(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class GoogleNewsRSS:
    name = "Google News RSS"

    def __init__(self, opener: Callable[[str, float], bytes] = _urlopen, timeout: float = 10):
        self._open, self._timeout = opener, timeout

    def fetch(self, aliases: list[str]) -> list[NewsItem]:
        q = {"q": search_query(aliases), "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        return parse_rss(self._open(RSS_URL + urllib.parse.urlencode(q), self._timeout))
