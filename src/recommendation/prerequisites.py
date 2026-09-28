"""Prerequisites checklist — M8 (DECISIONS #18).

What was checked before a stock is listed, in funnel order, and how each
check came out: PASS / WARN / FAIL / NA (input unavailable) / NOT_CHECKED
(not built yet). Details are built from computed values only; no prices.
News stays NOT_CHECKED until M9 supplies sourced news.
"""

from __future__ import annotations

from src.market.context import MarketContext
from src.quantitative.in_play import InPlayResult
from src.quantitative.liquidity import Liquidity
from src.quantitative.setups import SetupSignal, SetupState

PASS, WARN, FAIL, NA, NOT_CHECKED = "PASS", "WARN", "FAIL", "NA", "NOT_CHECKED"
SYMBOL = {PASS: "✓", WARN: "~", FAIL: "✗", NA: "–", NOT_CHECKED: "–"}
LABEL = {"in_play": "in play"}
SECTOR_STATUS = {"CONFIRMED": PASS, "NEUTRAL": WARN, "WEAK": FAIL, "UNAVAILABLE": NA}


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def _technicals(best: SetupSignal | None) -> dict:
    if best is None or best.state is SetupState.NONE:
        return _check("technicals", FAIL, "no active setup")
    state = best.state.value.lower()
    if best.state in (SetupState.TRIGGERED, SetupState.FORMING):
        return _check("technicals", PASS, f"{best.name} {state}")
    if best.state is SetupState.EXTENDED:
        return _check("technicals", WARN, f"{best.name} extended")
    return _check("technicals", FAIL, f"{best.name} {state}")


def _in_play(ip: InPlayResult) -> dict:
    rvol = f"RVOL {ip.rvol:.1f}x" if ip.rvol is not None else "RVOL unavailable"
    if ip.is_in_play:
        return _check("in_play", PASS, f"{rvol}, in-play score {ip.score:.0f}")
    return _check("in_play", FAIL, f"{ip.reason}; {rvol}")


def _liquidity(liq: Liquidity) -> dict:
    if liq.spread_pct is None:
        return _check("liquidity", WARN, "meets filters; spread unchecked (no fresh depth)")
    return _check("liquidity", PASS, f"meets filters; spread {liq.spread_pct:.2f}%")


def _market(market: MarketContext) -> dict:
    if market.status != "available" or market.score is None:
        return _check("market", NA, "NIFTY data unavailable")
    detail = f"NIFTY {market.nifty_change_pct:+.2f}% since open"
    return _check("market", PASS if market.score >= 50 else WARN, detail)


def build_checklist(best: SetupSignal | None, ip: InPlayResult, liq: Liquidity,
                    sector: dict, market: MarketContext) -> tuple[tuple[dict, ...], str]:
    checks = (
        _technicals(best),
        _in_play(ip),
        _liquidity(liq),
        _check("sector", SECTOR_STATUS[sector["verdict"]], sector["reason"]),
        _market(market),
        _check("news", NOT_CHECKED, "not checked yet (M9)"),
    )
    summary = "Checked: " + " · ".join(
        f"{LABEL.get(c['check'], c['check'])} {SYMBOL[c['status']]} {c['detail']}"
        for c in checks)
    return checks, summary
