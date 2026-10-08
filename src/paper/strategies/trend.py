"""Intraday trend: named setups confirmed by indicators — DECISIONS #31. SIMULATION ONLY.

For trades meant to last longer than a scalp (no time stop; exits at the
stop, the target or the 15:15 square-off). Bars arrive oriented (shorts on
the mirrored chart), so every rule reads "long".

1. Setup — the existing detectors (src/quantitative/setups.py) on 5-min
   bars: ORB15, VWAP_RECLAIM, VWAP_PULLBACK, EMA_PULLBACK, PDH (when the
   daily prep is known). A trade needs a FRESH trigger: TRIGGERED on the
   5-min bar that just closed and not at the previous 5-min check.
2. Confirmation — every one on today's 1-min bars (enough history early in
   the day): close above VWAP, EMA9 above EMA20, ADX ≥ `adx_min`, RSI within
   `rsi_range`, Supertrend up. Any indicator still warming up → no trade.
3. Stop `stop_mult` x the average true range of today's 5-min bars (at least
   `min_5m_bars`), target `target_r` x risk. Starting values, unvalidated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from src.data.candles import resample
from src.data.models import SESSION_OPEN
from src.indicators.core import adx, ema, rsi, supertrend, true_range, vwap
from src.market.sentiment import SentimentConfig
from src.paper.strategies.common import DirectionalStrategy, mirror_prep, to_candles
from src.quantitative.setups import (
    SetupState,
    ema_pullback,
    opening_range_breakout,
    pdh_breakout,
    vwap_pullback,
    vwap_reclaim,
)


@dataclass(frozen=True)
class TrendConfig:
    timeframe: int = 5
    setups: tuple = ("ORB15", "VWAP_RECLAIM", "VWAP_PULLBACK", "EMA_PULLBACK", "PDH")
    adx_min: float = 20.0
    rsi_range: tuple = (50.0, 75.0)
    stop_mult: float = 1.0
    min_5m_bars: int = 3
    ext_mult: float = 1.0           # EXTENDED when this x 5-min ATR past the trigger
    target_r: float = 2.0
    risk_per_trade: float = 500.0
    max_position_value: float = 25_000.0
    max_trades_per_symbol: int = 2
    first_entry: time = time(9, 30)
    last_entry: time = time(14, 30)

    @classmethod
    def from_dict(cls, d: dict | None) -> TrendConfig:
        d = dict(d or {})
        for k in ("first_entry", "last_entry"):
            if k in d:
                d[k] = time.fromisoformat(str(d[k]))
        for k in ("setups", "rsi_range"):
            if k in d:
                d[k] = tuple(d[k])
        return cls(**d)


def indicators_agree(c1, cfg: TrendConfig) -> str | None:
    """None when every confirmation holds, else the first reason it fails."""
    closes = [c.close for c in c1]
    vals = {"vwap": vwap(c1)[-1], "ema9": ema(closes, 9)[-1], "ema20": ema(closes, 20)[-1],
            "adx": adx(c1, 14)[-1], "rsi": rsi(closes, 14)[-1],
            "supertrend": supertrend(c1, 10, 3.0)[1][-1]}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        return f"warming up: {', '.join(missing)}"
    last = closes[-1]
    if last <= vals["vwap"]:
        return "below VWAP"
    if vals["ema9"] <= vals["ema20"]:
        return "EMA9 not above EMA20"
    if vals["adx"] < cfg.adx_min:
        return "ADX too low"
    if not cfg.rsi_range[0] <= vals["rsi"] <= cfg.rsi_range[1]:
        return "RSI out of range"
    if not vals["supertrend"]:
        return "Supertrend down"
    return None


class IntradayTrend(DirectionalStrategy):
    name = "TREND"
    entry_tag = "TREND"

    def __init__(self, cfg: TrendConfig = TrendConfig(),
                 sentiment_cfg: SentimentConfig = SentimentConfig()) -> None:
        super().__init__(cfg.risk_per_trade, cfg.max_position_value, cfg.target_r,
                         cfg.max_trades_per_symbol, cfg.first_entry, cfg.last_entry, None,
                         sentiment_cfg)
        self.cfg = cfg
        self._prev_state: dict = {}

    def on_day_start(self, day, ctx) -> None:
        super().on_day_start(day, ctx)
        self._prev_state = {}

    def params(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, time) else list(v) if isinstance(v, tuple)
                    else v) for k, v in self.cfg.__dict__.items()}

    def _bar_closes_bucket(self, ts: datetime) -> bool:
        start = datetime.combine(ts.date(), SESSION_OPEN)
        return int((ts - start).total_seconds() // 60 + 1) % self.cfg.timeframe == 0

    def signal(self, sym, bars, k, ctx, direction):
        if not self._bar_closes_bucket(bars[-1].ts):
            return None
        c1 = to_candles(bars)
        now = bars[-1].ts + timedelta(minutes=1)
        c5 = [c for c in resample(c1, self.cfg.timeframe, now) if c.is_complete]
        if len(c5) < self.cfg.min_5m_bars:
            return None
        tr = true_range(c5)[-14:]
        atr5 = sum(tr) / len(tr)
        if atr5 <= 0:
            return None
        ext = self.cfg.ext_mult * atr5
        states = self._states(sym, c5, ext, ctx, direction, k)
        prev = self._prev_state.get((sym, direction), {})
        self._prev_state[(sym, direction)] = states
        fresh = [n for n in self.cfg.setups if states.get(n) is SetupState.TRIGGERED
                 and prev.get(n) is not SetupState.TRIGGERED]
        if not fresh or indicators_agree(c1, self.cfg) is not None:
            return None
        return fresh[0], bars[-1].close, self.cfg.stop_mult * atr5

    def _states(self, sym, c5, ext, ctx, direction, k) -> dict:
        out = {}
        if "ORB15" in self.cfg.setups:
            out["ORB15"] = opening_range_breakout(c5, 15, ext).state
        if "VWAP_RECLAIM" in self.cfg.setups:
            out["VWAP_RECLAIM"] = vwap_reclaim(c5, ext).state
        if "VWAP_PULLBACK" in self.cfg.setups:
            out["VWAP_PULLBACK"] = vwap_pullback(c5, ext).state
        if "EMA_PULLBACK" in self.cfg.setups:
            out["EMA_PULLBACK"] = ema_pullback(c5, ext).state
        prep = (ctx.meta.get("preps") or {}).get(sym)
        if "PDH" in self.cfg.setups and prep is not None:
            p = prep if direction == "LONG" else mirror_prep(prep, k)
            out["PDH"] = pdh_breakout(c5, p, ext).state
        return out
