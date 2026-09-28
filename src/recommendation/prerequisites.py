"""Prerequisites checklist — M8 (DECISIONS #18).

What was checked before a stock is listed, in funnel order, and how each
check came out: PASS / WARN / FAIL / NA (input unavailable) / NOT_CHECKED
(not built yet). Details are built from computed values only; no prices.
News (M9): real linked headlines only; replay has no news → NOT_CHECKED.
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


def _when(item: dict) -> str:
    extra = item["outlets"] - 1
    more = f" +{extra} outlet{'s' if extra > 1 else ''}" if extra else ""
    return f"({item['source']}{more}, {item['published_at'][11:16]})"


def _news(news: dict | None) -> dict:
    if news is None:
        return _check("news", NOT_CHECKED, "not checked (no live news source)")
    verdict = news.get("verdict")
    items = news.get("items") or []
    if verdict == "UNAVAILABLE":
        return _check("news", NA, f"news unavailable: {news.get('reason', '')}".rstrip(": "))
    if verdict == "NO_RELEVANT_INFORMATION":
        return _check("news", NA, f"no relevant news in the last {news['lookback_hours']:g}h")
    top = {d: next((i for i in items if i["direction"] == d), None) for d in ("UP", "DOWN")}
    if verdict == "POSITIVE" and top["UP"]:
        return _check("news", PASS, f"upward catalyst: '{top['UP']['title']}' {_when(top['UP'])}")
    if verdict == "NEGATIVE" and top["DOWN"]:
        return _check("news", FAIL, f"downward: '{top['DOWN']['title']}' {_when(top['DOWN'])}")
    if verdict == "MIXED":
        return _check("news", WARN, f"mixed: {news.get('up_weight', 0)} up / "
                                    f"{news.get('down_weight', 0)} down outlet mentions")
    return _check("news", NA, "relevant news, no clear direction")


def build_checklist(best: SetupSignal | None, ip: InPlayResult, liq: Liquidity,
                    sector: dict, market: MarketContext,
                    news: dict | None = None) -> tuple[tuple[dict, ...], str]:
    checks = (
        _technicals(best),
        _in_play(ip),
        _liquidity(liq),
        _check("sector", SECTOR_STATUS[sector["verdict"]], sector["reason"]),
        _market(market),
        _news(news),
    )
    summary = "Checked: " + " · ".join(
        f"{LABEL.get(c['check'], c['check'])} {SYMBOL[c['status']]} {c['detail']}"
        for c in checks)
    return checks, summary
