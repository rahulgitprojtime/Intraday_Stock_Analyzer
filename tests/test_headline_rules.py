import pytest

from src.qualitative.headline_rules import DOWN, NEUTRAL, UP, NewsRules, classify_headline
from src.utils.config import load_yaml

RULES = NewsRules.from_dict(load_yaml("news.yaml"))


def c(title, symbol, source="Economic Times"):
    return classify_headline(title, source, symbol, RULES)


# Real headlines from Google News RSS, 2026-09-28.
def test_real_analyst_cut_is_downward():
    r = c("TCS In Focus: Citi Cuts Target, Retains 'Sell' Rating Amid Sector Challenges — "
          "Key Details Inside", "TCS", "NDTV Profit")
    assert (r.kind, r.direction) == ("catalyst", DOWN) and r.phrase in ("cuts target", "sell rating")


def test_real_stock_picks_list_is_roundup():
    r = c("Stock Picks Today: TCS, SBI, BPCL, Lenskart, PB Fintech And More On Brokerages' Radar",
          "TCS", "NDTV Profit")
    assert (r.kind, r.direction) == ("roundup", NEUTRAL)


def test_real_multi_stock_52_week_lows_is_roundup():
    r = c("RIL, Jio Fin, Maruti, Tata Consumer, TaMo PV, HUL, Wipro hit 52-week lows",
          "RELIANCE", "Business Standard")
    assert r.kind == "roundup"


def test_real_other_company_price_page_is_irrelevant_or_skipped():
    assert c("Infosys Share Price - Live NSE: INFY Stock Price & Chart", "TCS", "Upstox").kind \
        in ("irrelevant", "skipped")
    assert c("Infosys Share Price - Live NSE: INFY Stock Price & Chart", "INFY", "Upstox").kind \
        == "skipped"


def test_real_unrelated_headline_is_irrelevant():
    r = c("Tax audit deadline September 30: Why AIS, TDS and GST figures may not match your ITR",
          "TCS", "Livemint")
    assert r.kind == "irrelevant" and not r.relevant


# Context rules.
def test_order_win_is_upward():
    r = c("L&T bags order worth ₹5,000 crore from NHAI for expressway project", "LT")
    assert (r.kind, r.direction, r.phrase) == ("catalyst", UP, "bags order")


def test_negation_neutralises():
    r = c("TCS denies report of tax probe, says no notice received", "TCS")
    assert (r.kind, r.direction) == ("catalyst", NEUTRAL) and "negated" in r.reason


def test_question_headline_is_speculation():
    assert c("Should you buy Titan shares after Q2 results?", "TITAN").kind == "speculation"


def test_phrase_nearest_the_stock_decides_direction():
    title = "Infosys shares jump 3% as TCS misses estimates"
    assert c(title, "INFY").direction == UP
    assert c(title, "TCS").direction == DOWN


def test_phrase_far_from_the_stock_does_not_count():
    title = ("Airtel launches new plan for customers across twelve circles while rival telecom "
             "operators' shares slide after regulator probe report")
    assert c(title, "BHARTIARTL").direction == UP          # "launches" is next to Airtel


def test_alias_is_whole_word_and_handles_ampersand():
    assert c("M&M shares rally 4% on strong SUV sales", "M&M").direction == UP
    assert c("Bank Nifty ends flat; banks mixed", "SBIN").kind == "irrelevant"
    assert not c("ITCHY markets as traders wait", "ITC").relevant


def test_no_direction_phrase_is_neutral_catalyst():
    r = c("Wipro to announce Q2 results on October 16", "WIPRO")
    assert (r.kind, r.direction) == ("catalyst", NEUTRAL) and r.phrase is None


@pytest.mark.parametrize("title", [
    "Sensex falls 588 points; Reliance, HDFC Bank, ICICI Bank drag",
    "Stocks to watch: Reliance Industries, Titan, NTPC",
])
def test_roundup_phrases_and_company_count(title):
    assert c(title, "RELIANCE").kind == "roundup"


def test_news_yaml_phrases_are_all_strings():
    """YAML reads a bare `no` as False; every phrase must stay a string."""
    d = load_yaml("news.yaml")
    lists = [v for v in d.values() if isinstance(v, list)] + list(d["aliases"].values())
    assert all(isinstance(x, str) for xs in lists for x in xs)
