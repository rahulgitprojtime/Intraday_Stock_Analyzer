from datetime import datetime, timedelta

from src.app.feed_watchdog import FeedConfig, FeedWatchdog
from src.utils.config import load_settings

T = datetime(2026, 9, 28, 11, 0, 0)
S = timedelta(seconds=1)


class StubFeed:
    def __init__(self, fail_starts=0):
        self.starts = self.restarts = 0
        self.fail_starts = fail_starts

    def _maybe_fail(self):
        if self.fail_starts:
            self.fail_starts -= 1
            raise ConnectionError("socket token failed")

    def start(self):
        self.starts += 1
        self._maybe_fail()

    def restart(self):
        self.restarts += 1
        self._maybe_fail()


def test_config_from_settings_yaml():
    assert FeedConfig.from_dict(load_settings()["feed"]) == FeedConfig()


def test_live_then_stale_then_down_triggers_restart():
    feed = StubFeed()
    wd = FeedWatchdog(feed, FeedConfig())
    wd.start(T)
    assert wd.check(T + 5 * S, last_any_tick_age_s=2) == "LIVE"
    assert wd.check(T + 40 * S, last_any_tick_age_s=35) == "STALE" and feed.restarts == 0
    assert wd.check(T + 70 * S, last_any_tick_age_s=65) == "DOWN" and feed.restarts == 1
    assert wd.restarts == 1


def test_no_tick_since_start_counts_from_start_time():
    feed = StubFeed()
    wd = FeedWatchdog(feed, FeedConfig())
    wd.start(T)
    assert wd.check(T + 20 * S, None) == "STALE"          # connected, no data yet
    assert wd.check(T + 61 * S, None) == "DOWN" and feed.restarts == 1


def test_at_most_one_restart_per_interval_and_tick_resets():
    feed = StubFeed()
    wd = FeedWatchdog(feed, FeedConfig())
    wd.start(T)
    wd.check(T + 61 * S, None)
    wd.check(T + 90 * S, None)                             # still silent, too soon
    assert feed.restarts == 1
    wd.check(T + 125 * S, None)                            # 64 s after restart
    assert feed.restarts == 2
    assert wd.check(T + 130 * S, last_any_tick_age_s=1) == "LIVE" and wd.failures == 0


def test_backoff_after_max_fast_restarts():
    feed = StubFeed()
    cfg = FeedConfig()
    wd = FeedWatchdog(feed, cfg)
    wd.start(T)
    t = T
    for _ in range(cfg.max_fast_restarts):
        t += 61 * S
        wd.check(t, None)
    assert feed.restarts == cfg.max_fast_restarts
    wd.check(t + 120 * S, None)                            # fast interval passed, slow not
    assert feed.restarts == cfg.max_fast_restarts
    wd.check(t + 301 * S, None)
    assert feed.restarts == cfg.max_fast_restarts + 1


def test_start_failure_is_down_and_retried_not_raised():
    feed = StubFeed(fail_starts=2)
    wd = FeedWatchdog(feed, FeedConfig())
    wd.start(T)
    assert wd.status == "DOWN" and "socket token failed" in wd.last_error
    wd.check(T + 61 * S, None)                             # restart also fails
    assert feed.restarts == 1 and wd.status == "DOWN"
    wd.check(T + 122 * S, None)                            # succeeds
    assert feed.restarts == 2 and wd.last_error is None


def test_outside_session_never_restarts():
    feed = StubFeed()
    wd = FeedWatchdog(feed, FeedConfig())
    night = datetime(2026, 9, 28, 16, 0, 0)
    wd.start(night)
    assert wd.check(night + 600 * S, None) == "OFF" and feed.restarts == 0


def test_index_ticks_alone_do_not_hide_lost_stock_subscriptions():
    """Live 2026-09-28: after a network drop NIFTY kept ticking but 23/24
    stocks went silent; health must also require stock coverage."""
    feed = StubFeed()
    wd = FeedWatchdog(feed, FeedConfig())
    wd.start(T)
    assert wd.check(T + 10 * S, last_any_tick_age_s=0.1, stock_coverage=1.0) == "LIVE"
    assert wd.check(T + 40 * S, last_any_tick_age_s=0.1, stock_coverage=0.04) == "STALE"
    assert wd.check(T + 71 * S, last_any_tick_age_s=0.1, stock_coverage=0.04) == "DOWN"
    assert feed.restarts == 1
    assert wd.check(T + 90 * S, last_any_tick_age_s=0.1, stock_coverage=0.9) == "LIVE"
