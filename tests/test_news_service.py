from datetime import datetime, timedelta

from src.qualitative.headline_rules import NewsRules
from src.qualitative.news_service import NewsConfig, NewsService
from src.qualitative.news_source import NewsItem
from src.utils.config import load_yaml

RAW = load_yaml("news.yaml")
RULES = NewsRules.from_dict(RAW)
T = datetime(2026, 9, 28, 11, 0)
M = timedelta(minutes=1)


class FakeSource:
    name = "fake"

    def __init__(self):
        self.calls, self.fail = [], set()

    def fetch(self, aliases):
        self.calls.append(aliases[0])
        if aliases[0] in self.fail:
            raise OSError("timed out")
        return [NewsItem(f"{aliases[0]} bags order worth Rs 900 crore", "Mint",
                         T - timedelta(hours=1), "https://x/1")]


def service(symbols=("TCS", "INFY", "WIPRO", "LT"), per_tick=2):
    src = FakeSource()
    cfg = NewsConfig.from_dict(RAW | {"fetch_per_tick": per_tick})
    return NewsService(src, RULES, cfg, RAW["aliases"], list(symbols)), src


def test_config_from_yaml():
    cfg = NewsConfig.from_dict(RAW)
    assert (cfg.lookback_hours, cfg.refresh_minutes, cfg.max_age_minutes, cfg.fetch_per_tick) \
        == (18, 10, 30, 3)


def test_staggered_fetch_never_fetched_first_then_oldest():
    svc, src = service()
    svc.tick(T)
    svc.tick(T + M)
    assert src.calls == ["TCS", "Infosys", "Wipro", "Larsen & Toubro"]
    svc.tick(T + 2 * M)                         # everyone fresh (< 10 min): nothing fetched
    assert len(src.calls) == 4
    svc.tick(T + 10 * M)
    assert src.calls[4:] == ["TCS", "Infosys"]                   # oldest refreshed first


def test_result_pending_before_first_fetch_then_verdict():
    svc, _ = service()
    assert svc.result("TCS", T) == {"status": "unavailable", "verdict": "PENDING", "items": [],
                                    "reason": "first news fetch pending"}
    assert svc.result("UNKNOWN", T) is None                      # no aliases: not checked
    svc.tick(T)
    r = svc.result("TCS", T + M)
    assert r["verdict"] == "POSITIVE" and r["source"] == "fake" and r["age_minutes"] == 1


def test_failed_fetch_keeps_last_good_until_max_age():
    svc, src = service(symbols=("TCS",), per_tick=1)
    svc.tick(T)
    src.fail.add("TCS")
    errors = svc.tick(T + 10 * M)
    assert errors == ["news TCS: OSError: timed out"]
    assert svc.result("TCS", T + 20 * M)["verdict"] == "POSITIVE"        # last good, age 20
    stale = svc.result("TCS", T + 31 * M)
    assert stale == {"status": "unavailable", "verdict": "UNAVAILABLE", "items": [],
                     "reason": "news older than 30 min (last fetch failed or pending)"}


def test_failed_first_fetch_is_unavailable_and_retried_after_refresh():
    svc, src = service(symbols=("TCS",), per_tick=1)
    src.fail.add("TCS")
    svc.tick(T)
    assert svc.result("TCS", T)["verdict"] == "UNAVAILABLE"
    svc.tick(T + M)
    assert len(src.calls) == 1                                   # no hammering
    src.fail.clear()
    svc.tick(T + 10 * M)
    assert svc.result("TCS", T + 10 * M)["verdict"] == "POSITIVE"


def test_add_symbols_for_new_universe_members():
    svc, src = service(symbols=("TCS",), per_tick=5)
    svc.add_symbols({"SUZLON": ["Suzlon Energy"], "TCS": ["ignored, already known"]})
    svc.tick(T)
    assert src.calls == ["TCS", "Suzlon Energy"]
    assert svc.result("SUZLON", T)["verdict"] == "POSITIVE"
