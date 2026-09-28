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
    title = ("Airtel bags enterprise contract for customers across twelve circles while rival "
             "telecom operators' shares slide after regulator probe report")
    assert c(title, "BHARTIARTL").direction == UP          # "bags" is next to Airtel


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


def test_gap_phrases_match_around_the_company_name():
    assert c("Citi cuts TCS target price, keeps sell rating", "TCS").phrase == "cuts * target"
    assert c("NTPC bags Rs 2,000 crore order from state utility", "NTPC").phrase == "bags * order"
    assert c("Jefferies raises Titan Company target on festive demand", "TITAN").direction == UP


# Live Google News headlines, 2026-09-28 13:50 — misclassified by the first
# rule set. Each is a regression case.
@pytest.mark.parametrize("title,symbol", [
    ("Reliance Power promoters confirm no encumbrance on shares", "RELIANCE"),
    ("Titan Travel launches 2027 Worldwide brochure to agents", "TITAN"),
    ("Tottenham keen on Bayern Munich titan with contact confirmed by insider", "TITAN"),
    ("Weekly Recap: ITC 337 DRAM probe and revenue from memory, device pricing", "ITC"),
    ("Fake GST Registration Shows ₹14.74 Crore Supplies, ₹2.65 Crore Bogus ITC Alleged", "ITC"),
    ("L&T Finance shares may rise 34% as Citi maintains Buy at ₹380", "LT"),
    ("NineDot opens 20 MW Bronx battery site to power 20,000 homes, boost New York's power grid",
     "POWERGRID"),
    ("NTPC Green Energy Launches 81.34 MW Solar Project in Rajasthan", "NTPC"),
    ("Dhoot Transmission shares fall for fourth day after Kotak warns of 10% fall on valuation",
     "KOTAKBANK"),
    ("UK Markets Brief: Airtel Money to IPO and Airtel Africa Plc listing postponed", "BHARTIARTL"),
    ("Accumulate Mahindra and Mahindra Financial Services; target of Rs 350", "M&M"),
])
def test_lookalike_companies_are_not_the_stock(title, symbol):
    assert not c(title, symbol).relevant


@pytest.mark.parametrize("title,symbol,direction", [
    ("HDFC Bank Shares Rise On UBS Buy Call; Q2 Results In Focus", "HDFCBANK", UP),
    ("Infosys Stock Update: Share Price Slips Marginally Amid IT Sector Weakness", "INFY", DOWN),
    ("Adani Enterprises shares drop 2% after Adani group entities swap 86 lakh shares",
     "ADANIENT", DOWN),
    ("Shrikant Chouhan recommends buying Torrent Pharma, selling Bharti Airtel", "BHARTIARTL",
     DOWN),
    ("ICICI Bank shares may rise 33% as Citi maintains Buy at ₹1,770", "ICICIBANK", UP),
    ("Sun Pharma bags rights to $3.7 billion cholesterol drug market globally", "SUNPHARMA", UP),
])
def test_live_direction_cases(title, symbol, direction):
    r = c(title, symbol)
    assert (r.kind, r.direction) == ("catalyst", direction)


@pytest.mark.parametrize("title,symbol", [
    ("IT sector stocks today, September 28: Nelco falls 2.31%, TCS down 1.10%; R Systems jumps",
     "TCS"),
    ("Adani Group stocks fall today, September 28; Adani Total Gas down 1.54%, Adani Enterprises",
     "ADANIENT"),
])
def test_live_sector_lists_are_roundups(title, symbol):
    assert c(title, symbol).kind == "roundup"


def test_subsidiary_product_news_is_relevant_but_not_directional():
    r = c("Reliance Jio launches JioShield safety subscription free via vouchers", "RELIANCE")
    assert r.relevant and r.direction == NEUTRAL


def test_product_launch_is_not_a_price_catalyst():
    assert c("Tata Steel launches new rebar brand for rural housing", "TATASTEEL").direction \
        == NEUTRAL


def test_common_word_alias_is_case_sensitive():
    r = c("Elice pursues 1 trillion-won Korea IPO as growth accelerates, reliance on state rises",
          "RELIANCE")
    assert not r.relevant
    assert c("Reliance shares rise 2% after AGM", "RELIANCE").direction == UP


def test_direction_phrase_must_share_the_stocks_clause():
    r = c("YES Bank shares fall 4% on Citi's negative watch; Axis Bank also in focus", "AXISBANK")
    assert r.relevant and r.direction == NEUTRAL
    assert c("HDFC Bank Shares Rise On UBS Buy Call; Q2 Results In Focus", "HDFCBANK").direction \
        == UP
