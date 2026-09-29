"""Backtest performance — DECISIONS #29. SIMULATION ONLY.

All P&L is after the full cost model. Win rate counts every round trip
(breakeven within one paisa counts as neither win nor loss). Max drawdown
is measured on the minute equity curve. Sharpe is annualised (x sqrt(252))
from daily returns of end-of-day equity with a zero risk-free rate; it is
NOT AVAILABLE with fewer than two days or zero variance. Measurements, not
probabilities of profit.
"""

from __future__ import annotations

from math import sqrt
from statistics import mean, stdev

from src.paper.metrics import BREAKEVEN_RUPEES, NA, SMALL_SAMPLE_N


def max_drawdown(equity: list[float], start: float) -> tuple[float, float]:
    peak, dd, dd_pct = start, 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak - e > dd:
            dd, dd_pct = peak - e, (peak - e) / peak * 100
    return round(dd, 4), round(dd_pct, 4)


def sharpe(daily_end_equity: list[float], start: float) -> float | str:
    eq = [start, *daily_end_equity]
    rets = [b / a - 1 for a, b in zip(eq, eq[1:]) if a]
    if len(rets) < 2 or stdev(rets) == 0:
        return NA
    return round(mean(rets) / stdev(rets) * sqrt(252), 4)


def performance(trades: list[dict], equity_curve: list[tuple], daily: list[dict],
                starting_capital: float) -> dict:
    n = len(trades)
    net = [t["net_pnl"] for t in trades]
    wins = [p for p in net if p >= BREAKEVEN_RUPEES]
    losses = [p for p in net if p <= -BREAKEVEN_RUPEES]
    end = daily[-1]["end_equity"] if daily else starting_capital
    dd, dd_pct = max_drawdown([e for _, e in equity_curve], starting_capital)
    return {
        "trades": n, "winners": len(wins), "losers": len(losses),
        "win_rate": round(len(wins) / n * 100, 2) if n else NA,
        "gross_pnl": round(sum(t["gross_pnl"] for t in trades), 2),
        "charges": round(sum(t["charges"] for t in trades), 2),
        "net_pnl": round(sum(net), 2),
        "average_net_pnl": round(mean(net), 2) if n else NA,
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else NA,
        "max_drawdown": dd, "max_drawdown_pct": dd_pct,
        "sharpe": sharpe([d["end_equity"] for d in daily], starting_capital),
        "starting_capital": starting_capital, "end_equity": round(end, 2),
        "return_pct": round((end / starting_capital - 1) * 100, 4),
        "days": len(daily),
        "sample_warning": "INSUFFICIENT SAMPLE SIZE" if n < SMALL_SAMPLE_N else None,
    }
