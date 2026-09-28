"""Outcome metrics — M10 (DECISIONS #20).

Every closed trade counts: winners, losers and breakeven alike. Anything
that cannot be computed is NOT AVAILABLE, never estimated. Groups under
SMALL_SAMPLE_N trades carry a warning; associations are observational.
"""

from __future__ import annotations

from statistics import mean, median

from src.recommendation.scoring import SETUP_FAMILIES

NA = "NOT AVAILABLE"
SMALL_SAMPLE_N = 30
BREAKEVEN_RUPEES = 0.01
TIME_BUCKETS = (("09:15", "09:30"), ("09:30", "10:30"), ("10:30", "11:30"), ("11:30", "13:30"),
                ("13:30", "14:30"), ("14:30", "15:00"), ("15:00", "15:30"))


def _closed(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t.get("exit_reason")]


def _max_drawdown(trades: list[dict]) -> float:
    equity = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t["exit_timestamp"]):     # stable: ties keep order
        equity += t["net_pnl"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def summarize(trades: list[dict]) -> dict:
    closed = _closed(trades)
    n = len(closed)
    base = {"trades": n, "open_at_close": len(trades) - n}
    if not n:
        return base | {k: NA for k in ("win_rate", "net_pnl", "average_pnl", "median_pnl",
                                       "average_r", "median_r", "profit_factor")} | {
            "winners": 0, "losers": 0, "breakeven": 0, "result": "NO TRADES",
            "sample_warning": "INSUFFICIENT SAMPLE SIZE"}
    pnl = [t["net_pnl"] for t in closed]
    wins = [p for p in pnl if p >= BREAKEVEN_RUPEES]
    losses = [p for p in pnl if p <= -BREAKEVEN_RUPEES]
    rs = [t["risk_multiple"] for t in closed if t.get("risk_multiple") is not None]
    return base | {
        "winners": len(wins), "losers": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate": len(wins) / n * 100,
        "gross_pnl": sum(t.get("gross_pnl", 0) for t in closed), "net_pnl": sum(pnl),
        "average_pnl": mean(pnl), "median_pnl": median(pnl),
        "average_winner": mean(wins) if wins else NA,
        "average_loser": mean(losses) if losses else NA,
        "largest_winner": max(wins) if wins else NA,
        "largest_loser": min(losses) if losses else NA,
        "profit_factor": sum(wins) / -sum(losses) if losses else NA,
        "average_r": mean(rs) if rs else NA, "median_r": median(rs) if rs else NA,
        "expectancy_pnl": mean(pnl), "max_drawdown": _max_drawdown(closed),
        "average_holding_minutes": mean(t["holding_minutes"] for t in closed),
        "result": "LOSS" if sum(pnl) < 0 else "PROFIT" if sum(pnl) > 0 else "FLAT",
        "sample_warning": "INSUFFICIENT SAMPLE SIZE" if n < SMALL_SAMPLE_N else None,
    }


def time_bucket(ts: str) -> str:
    hhmm = ts[11:16]
    for lo, hi in TIME_BUCKETS:
        if lo <= hhmm < hi:
            return f"{lo}-{hi}"
    return "outside session"


def _market_band(t: dict) -> str:
    score = (t.get("market_context") or {}).get("score")
    if score is None:
        return NA
    return "weak (<40)" if score < 40 else "neutral (40-60)" if score <= 60 else "strong (>60)"


def _news(t: dict) -> str:
    q = t.get("qualitative_context") or {}
    return q.get("verdict") or "NOT_AVAILABLE"


def _group_stats(ts: list[dict]) -> dict:
    s = summarize(ts)
    return {"trades": s["trades"], "win_rate": s["win_rate"], "average_r": s["average_r"],
            "net_pnl": s["net_pnl"],
            "sample_warning": "SMALL SAMPLE" if s["trades"] < SMALL_SAMPLE_N else None}


def _group(trades: list[dict], key) -> dict:
    groups: dict[str, list] = {}
    for t in trades:
        groups.setdefault(str(key(t)), []).append(t)
    return {k: _group_stats(v) for k, v in sorted(groups.items())}


def breakdowns(trades: list[dict]) -> dict:
    closed = _closed(trades)
    by_time = _group(closed, lambda t: time_bucket(t["entry_timestamp"]))
    empty = _group_stats([])
    return {
        "setup": _group(closed, lambda t: t.get("best_setup")),
        "setup_family": _group(closed, lambda t: SETUP_FAMILIES.get(t.get("best_setup"), NA)),
        "sector": _group(closed, lambda t: (t.get("sector_context") or {}).get("verdict", NA)),
        "news": _group(closed, _news),
        "market_context": _group(closed, _market_band),
        "exit_reason": _group(closed, lambda t: t["exit_reason"]),
        "time_of_day": {f"{lo}-{hi}": by_time.get(f"{lo}-{hi}", empty) for lo, hi in TIME_BUCKETS},
    }
