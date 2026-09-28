from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.qualitative.headline_rules import NewsRules
from src.qualitative.news_check import aggregate_news
from src.qualitative.news_source import GoogleNewsRSS, NewsItem, parse_rss, search_query
from src.utils.config import load_yaml

CFG = load_yaml("news.yaml")
RULES = NewsRules.from_dict(CFG)
XML = Path("tests/fixtures/google_news_tcs.xml").read_bytes()
NOW = datetime(2026, 9, 28, 11, 0)          # IST, naive local


def test_parse_rss_strips_source_suffix_and_converts_to_local_time():
    items = parse_rss(XML)
    assert len(items) == 6
    first = items[0]
    assert first.title == ("TCS In Focus: Citi Cuts Target, Retains 'Sell' Rating Amid Sector "
                           "Challenges")
    assert first.source == "NDTV Profit" and first.link == "https://news.google.com/rss/articles/AAA1"
    assert first.published_at == datetime(2026, 9, 28, 7, 54, 16)         # 02:24 GMT = 07:54 IST


def test_parse_rss_bad_xml_raises_value_error():
    with pytest.raises(ValueError):
        parse_rss(b"<html>blocked</html")


def test_search_query_uses_quoted_aliases():
    assert search_query(["Tata Consultancy Services", "TCS"]) == \
        '"Tata Consultancy Services" OR "TCS" when:1d'


def test_source_fetch_uses_injected_opener_and_parses():
    seen = {}

    def opener(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return XML

    items = GoogleNewsRSS(opener=opener, timeout=7).fetch(["TCS"])
    assert len(items) == 6 and seen["timeout"] == 7
    assert seen["url"].startswith("https://news.google.com/rss/search?q=%22TCS%22+when%3A1d")


def test_aggregate_merges_same_story_and_decides_direction():
    v = aggregate_news(parse_rss(XML), "TCS", RULES, NOW, lookback_hours=18, max_items=5)
    assert v["status"] == "available"
    stories = v["items"]
    citi = next(s for s in stories if "Citi" in s["title"])
    assert citi["direction"] == "DOWN" and citi["outlets"] == 2          # NDTV + ET merged
    deal = next(s for s in stories if "deal" in s["title"])
    assert deal["direction"] == "UP" and deal["outlets"] == 1
    assert v["up_weight"] == 1 and v["down_weight"] == 2
    assert v["verdict"] == "NEGATIVE"
    titles = " ".join(s["title"] for s in stories)
    assert "Infosys" not in titles and "Old News" not in titles              # skipped / too old
    assert v["counts"] == {"catalyst": 2, "roundup": 1, "speculation": 0}
    assert all(s["link"].startswith("https://") for s in stories)


def test_aggregate_positive_mixed_neutral_and_none():
    def item(title, src="Mint", h=1):
        return NewsItem(title, src, NOW - timedelta(hours=h), "https://x/" + title[:5])
    up = [item("TCS bags $500 million deal from insurer")]
    assert aggregate_news(up, "TCS", RULES, NOW, 18, 5)["verdict"] == "POSITIVE"
    mixed = up + [item("Citi cuts TCS target price", "ET")]
    assert aggregate_news(mixed, "TCS", RULES, NOW, 18, 5)["verdict"] == "MIXED"
    neutral = [item("Wipro to announce Q2 results on October 16")]
    assert aggregate_news(neutral, "WIPRO", RULES, NOW, 18, 5)["verdict"] == "NEUTRAL"
    none = aggregate_news([], "TCS", RULES, NOW, 18, 5)
    assert none["verdict"] == "NO_RELEVANT_INFORMATION" and none["items"] == []
