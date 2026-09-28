"""Eight scoring groups — M12 (DECISIONS #22, user-defined criteria).

Each group is 0-100 from its sub-signals (`parts`, each 0-100 or None).
A group averages the parts it has; with none it is None and drops out of
the final blend (never counted as zero). Pure functions over closed bars
and existing outputs — no prices in the output (DECISIONS #11).

  PRICE     change vs previous close, range expansion vs daily ATR, position
            in today's range, distance above VWAP, breakout structure
  VOLUME    time-of-day RVOL, volume acceleration (last 5 bars vs prior 15)
  MOMENTUM  short ROC, ROC acceleration, EMA 9/20/50 stacking, RSI (above 80
            = overextended, capped at 50), ADX (only when price is above
            EMA20 — ADX is direction-agnostic and this is long-only)
  SETUP     existing setup score + setup-family confluence
  MARKET    NIFTY score, regime (NIFTY vs its EMA20), BANK NIFTY for banks
  SECTOR    sector score, stock vs sector
  LIQUIDITY liquidity score, spread (live depth), microstructure (Scalp)
  NEWS      POSITIVE 100 / NEUTRAL or none relevant 50 / MIXED 40 / NEGATIVE 0
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.data.models import Candle
from src.indicators.core import adx, ema, roc, rsi, vwap
from src.market.context import MarketContext
from src.quantitative.daily_prep import DailyPrep
from src.quantitative.liquidity import Liquidity
from src.quantitative.microstructure import SymbolFeed

NEWS_POINTS = {"POSITIVE": 100.0, "NEUTRAL": 50.0, "NO_RELEVANT_INFORMATION": 50.0,
               "MIXED": 40.0, "NEGATIVE": 0.0}


@dataclass(frozen=True)
class GroupScore:
    name: str
    value: float | None
    parts: dict


def _ramp(x: float | None, lo: float, hi: float) -> float | None:
    if x is None:
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _group(name: str, parts: dict) -> GroupScore:
    have = [v for v in parts.values() if v is not None]
    return GroupScore(name, sum(have) / len(have) if have else None, parts)


def _last(series):
    return series[-1] if series else None


def price_group(bars: Sequence[Candle], prep: DailyPrep | None,
                range_expansion: float | None) -> GroupScore:
    if not bars:
        return _group("movement", {})
    close = bars[-1].close
    high, low = max(b.high for b in bars), min(b.low for b in bars)
    prev = prep.prev_close if prep else None
    change = (close / prev - 1) * 100 if prev else None
    pos = (close - low) / (high - low) if high > low else None
    vw = _last(vwap(bars))
    vwap_dist = (close / vw - 1) * 100 if vw else None
    or_high = max(b.high for b in bars[:15]) if len(bars) > 15 else None
    if prep and close > prep.prev_high:
        structure = 100.0
    elif or_high is not None and close > or_high:
        structure = 70.0
    elif close > bars[0].open:
        structure = 30.0
    else:
        structure = 0.0
    return _group("movement", {"change": _ramp(change, 0, 3), "range": _ramp(range_expansion, 0.3, 1.0),
                            "position": _ramp(pos, 0.5, 1.0), "vwap": _ramp(vwap_dist, 0, 1),
                            "structure": structure})


def volume_group(rvol: float | None, bars: Sequence[Candle]) -> GroupScore:
    accel = None
    if len(bars) >= 20:
        prior = sum(b.volume for b in bars[-20:-5]) / 15
        recent = sum(b.volume for b in bars[-5:])
        accel = recent / (prior * 5) if prior > 0 else None
    return _group("volume", {"rvol": _ramp(rvol, 1, 4), "acceleration": _ramp(accel, 1, 3)})


def momentum_group(bars: Sequence[Candle], roc_full_pct: float) -> GroupScore:
    closes = [b.close for b in bars]
    r = roc(closes, 5)
    now, before = _last(r), (r[-6] if len(r) >= 6 else None)
    accel = now - before if now is not None and before is not None else None
    e9, e20, e50 = _last(ema(closes, 9)), _last(ema(closes, 20)), _last(ema(closes, 50))
    checks = [c for c in ((closes[-1] > e9) if e9 else None, (e9 > e20) if e9 and e20 else None,
                          (e20 > e50) if e20 and e50 else None) if c is not None] if closes else []
    rs = _last(rsi(closes)) if closes else None
    rsi_pts = None if rs is None else (50.0 if rs > 80 else _ramp(rs, 50, 70))
    ax = _last(adx(bars)) if bars else None
    uptrend = e20 is not None and closes[-1] > e20
    adx_pts = None if ax is None else (_ramp(ax, 15, 35) if uptrend else 0.0)
    return _group("momentum", {
        "roc": _ramp(now, 0, roc_full_pct), "acceleration": _ramp(accel, 0, roc_full_pct / 2),
        "ema_structure": sum(checks) / len(checks) * 100 if checks else None,
        "rsi": rsi_pts, "adx": adx_pts})


def setup_group(setup_score: float, confluence_bonus: float) -> GroupScore:
    return GroupScore("setup", min(100.0, setup_score + confluence_bonus),
                      {"setup": setup_score, "confluence": confluence_bonus})


def market_group(market: MarketContext, index_bars: Sequence[Candle],
                 bank: MarketContext | None) -> GroupScore:
    closes = [b.close for b in index_bars if b.is_complete]
    e20 = _last(ema(closes, 20))
    regime = None if e20 is None else (100.0 if closes[-1] > e20 else 0.0)
    nifty = market.score if market.status == "available" else None
    bank_pts = bank.score if bank is not None and bank.status == "available" else None
    return _group("market", {"nifty": nifty, "regime": regime, "bank_nifty": bank_pts})


def sector_group(sector: dict) -> GroupScore:
    if sector.get("status") != "available":
        return _group("sector", {"sector": None, "stock_vs_sector": None})
    return _group("sector", {"sector": sector.get("sector_score"),
                             "stock_vs_sector": _ramp(sector.get("stock_vs_sector"), -0.5, 0.5)})


def liquidity_group(liq: Liquidity, feed: SymbolFeed | None, mode: str) -> GroupScore:
    spread = None if liq.spread_pct is None else _ramp(-liq.spread_pct, -0.5, -0.05)
    micro = feed.micro_score if (feed is not None and mode == "SCALP") else None
    return _group("liquidity", {"liquidity": liq.score, "spread": spread, "microstructure": micro})


def news_group(news: dict | None) -> GroupScore:
    verdict = (news or {}).get("verdict")
    return GroupScore("news", NEWS_POINTS.get(verdict), {"verdict": verdict})
