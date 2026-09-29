import random

from src.research.evaluate import (
    HORIZONS,
    bootstrap_diff,
    daily_ic,
    report,
    residualize,
    spearman,
    verdict,
)


def rows(n_days, per_day, effect, seed, noise=0.3, **extra):
    """Rows whose excess return = effect + noise at every horizon."""
    rng = random.Random(seed)
    out = []
    for d in range(n_days):
        for i in range(per_day):
            r = {"day": f"2026-01-{d + 1:02d}" if d < 31 else f"2026-02-{d - 30:02d}",
                 "symbol": f"S{i}", "mode": "DAY", "trigger": "panel", **extra}
            for h in HORIZONS:
                r[f"xs_nifty_{h}"] = effect + rng.gauss(0, noise)
            out.append(r)
    return out


def test_spearman_handles_ties_and_constant_input():
    assert spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == 1.0
    assert spearman([1, 1, 2, 2, 3], [5, 5, 4, 4, 3]) == -1.0
    assert spearman([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) is None


def test_bootstrap_interval_excludes_zero_only_for_a_real_difference():
    good, bad = rows(30, 5, 0.3, 1), rows(30, 5, 0.0, 2)
    d, lo, hi = bootstrap_diff(good, bad, "xs_nifty_15")
    assert 0.2 < d < 0.4 and lo > 0
    d, lo, hi = bootstrap_diff(rows(30, 5, 0.0, 3), bad, "xs_nifty_15")
    assert lo < 0 < hi


def test_verdict_insufficient_finding_and_no_evidence():
    assert verdict(rows(5, 10, 0.3, 1), rows(5, 10, 0.0, 2)) == "INSUFFICIENT"
    assert verdict(rows(30, 5, 0.3, 1), rows(30, 5, 0.0, 2)) == "FINDING: better"
    assert verdict(rows(30, 5, -0.3, 1), rows(30, 5, 0.0, 2)) == "FINDING: worse"
    assert verdict(rows(30, 5, 0.0, 4), rows(30, 5, 0.0, 5)).startswith("NO EVIDENCE")


def test_daily_ic_averages_within_day_correlations():
    rs = []
    for d in range(10):
        for i in range(10):
            rs.append({"day": f"d{d}", "x": i, "xs_nifty_5": i * 0.1})
    ic, t, n = daily_ic(rs, lambda r: r["x"], "xs_nifty_5")
    assert ic == 1.0 and n == 10


def test_residualize_removes_what_technicals_explain():
    """Outcome driven purely by the technical score; news label is random →
    news must show no information beyond technicals."""
    rng = random.Random(9)
    rs = []
    for d in range(30):
        for i in range(20):
            tech = rng.uniform(0, 100)
            r = {"day": f"2026-01-{d + 1:02d}" if d < 31 else "x", "g_setup": tech,
                 "news_verdict": rng.choice(["POSITIVE", "NO_RELEVANT_INFORMATION"])}
            for h in HORIZONS:
                r[f"xs_nifty_{h}"] = tech / 100 + rng.gauss(0, 0.05)
            rs.append(r)
    res = residualize(rs)
    assert "res_5" not in rs[0]                                      # inputs untouched
    pos = [r for r in res if r["news_verdict"] == "POSITIVE"]
    nri = [r for r in res if r["news_verdict"] == "NO_RELEVANT_INFORMATION"]
    assert verdict(pos, nri, metric="res").startswith("NO EVIDENCE")


def test_report_runs_and_flags_small_samples():
    rs = rows(3, 4, 0.1, 1, g_movement=50.0, g_setup=60.0, rvol=2.0, score=70.0,
              sector_verdict="CONFIRMED", news_verdict="NOT_CHECKED", confluence_count=2,
              raw_movement_day_change_pct=1.5, base_score=70.0, lunch_penalty=False,
              as_of="2026-01-01T10:00:00", setup_ORB15="TRIGGERED", triggered=["ORB15"])
    text = report(rs, "t")
    assert "3 day(s): description, not evidence" in text
    assert text.count("## ") == 11                       # 10 questions, score bands per mode


def test_load_rows_keeps_panel_rows_and_only_needed_columns(tmp_path):
    import json
    from src.research.evaluate import load_rows
    row = {"day": "2026-01-05", "as_of": "2026-01-05T10:00:00", "symbol": "A", "mode": "DAY",
           "trigger": "panel", "score": 70.0, "g_volume": 50.0, "setup_ORB15": "NONE",
           "xs_nifty_5": 0.1, "p_volume_rvol": 3.0, "fwd_5": 0.2, "config_hash": "x"}
    (tmp_path / "2026-01-05.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(row | {"trigger": "event"}) + "\n")
    [r] = load_rows(tmp_path)
    assert r["g_volume"] == 50.0 and r["setup_ORB15"] == "NONE" and r["xs_nifty_5"] == 0.1
    assert "p_volume_rvol" not in r and "fwd_5" not in r and "config_hash" not in r


def test_round2_report_has_the_six_questions():
    from src.research.evaluate import report
    rs = rows(3, 4, 0.1, 1, as_of="2026-01-01T10:00:00", raw_movement_vwap_dist_pct=0.3,
              raw_momentum_rsi=65.0, raw_momentum_adx=25.0, raw_momentum_roc5_pct=0.4,
              nifty_change_pct=0.2)
    text = report(rs, "t", round_=2)
    assert text.count("## ") == 6 and "## 11." in text and "## 16." in text
