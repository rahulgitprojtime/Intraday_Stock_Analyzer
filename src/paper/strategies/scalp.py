"""Scalp: pure price action on 1-min bars — DECISIONS #31. SIMULATION ONLY.

No indicators: only candles, ranges and swing structure. Bars arrive
oriented (shorts on the mirrored chart, common.py), so every rule reads
"long". A trade needs ONE clear setup on the bar that just closed:

1. TIGHT_BREAKOUT — the previous `consolidation_bars` bars sit in a range no
   wider than `tight_mult` x the average bar range, and the signal bar
   closes above that range: a strong bar (body ≥ `body_min` of its range,
   close in the top `close_zone` of it, range ≥ `expansion_min` x average),
   closing at a new high of the last `swing_lookback` bars and above the
   day's open. Stop: below the consolidation low.
2. HIGHER_LOW — the last swing high topped the one before it (higher high),
   price pulled back for ≥ 2 bars to a low above the previous swing low
   (higher low), and the signal bar closes up through the prior bar's high,
   in its upper half. Stop: below the pullback low.

Stop buffer `stop_buffer` x average range; skipped when the stop is wider
than `max_risk_mult` x average range (not a scalp). Target `target_r` x
risk; exit after `max_hold_minutes` if neither is hit. Starting values,
unvalidated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from src.paper.orders import Bar
from src.paper.strategies.common import DirectionalStrategy, rules_from, swing_points
from src.market.sentiment import SentimentConfig


@dataclass(frozen=True)
class ScalpConfig:
    consolidation_bars: int = 6
    range_lookback: int = 20
    min_bars: int = 10            # bars of today needed before any signal
    tight_mult: float = 2.5
    body_min: float = 0.6
    close_zone: float = 0.3
    expansion_min: float = 1.2
    swing_lookback: int = 20
    stop_buffer: float = 0.1
    max_risk_mult: float = 4.0
    target_r: float = 2.0
    max_hold_minutes: float = 15
    risk_per_trade: float = 1000.0
    max_position_value: float = 49_000.0
    max_trades_per_symbol: int = 1
    max_trades_per_day: int | None = 20
    # shared entry/exit rules (DECISIONS #33)
    stop_atr_mult: float = 1.0
    min_stop_pct: float = 0.15
    rs_filter: bool = True
    structure_filter: bool = True
    min_reward_cost_mult: float = 3.0
    first_entry: time = time(9, 25)
    last_entry: time = time(15, 0)

    @classmethod
    def from_dict(cls, d: dict | None) -> ScalpConfig:
        d = dict(d or {})
        for k in ("first_entry", "last_entry"):
            if k in d:
                d[k] = time.fromisoformat(str(d[k]))
        return cls(**d)


def avg_range(bars: list[Bar]) -> float:
    return sum(b.high - b.low for b in bars) / len(bars) if bars else 0.0


def tight_breakout(bars: list[Bar], cfg: ScalpConfig) -> float | None:
    """Stop level, or None."""
    n = cfg.consolidation_bars
    if len(bars) < max(cfg.min_bars, n + 2):
        return None
    sig, cons = bars[-1], bars[-1 - n:-1]
    avg = avg_range(bars[-1 - cfg.range_lookback:-1])
    hi, lo = max(b.high for b in cons), min(b.low for b in cons)
    rng = sig.high - sig.low
    if avg <= 0 or rng <= 0 or hi - lo > cfg.tight_mult * avg:
        return None
    if not (sig.close > hi and sig.close > sig.open
            and sig.close - sig.open >= cfg.body_min * rng
            and sig.high - sig.close <= cfg.close_zone * rng
            and rng >= cfg.expansion_min * avg):
        return None
    if sig.close <= bars[0].open:                                  # below the day's open
        return None
    if sig.close < max(b.high for b in bars[-1 - cfg.swing_lookback:-1]):
        return None                                                # not a new local high
    return lo - cfg.stop_buffer * avg


def higher_low(bars: list[Bar], cfg: ScalpConfig) -> float | None:
    if len(bars) < max(cfg.min_bars, 8):
        return None
    window = bars[-1 - cfg.swing_lookback:-1]
    highs, lows = swing_points(window)
    if len(highs) < 2 or not lows:
        return None
    h1, h2 = highs[-2], highs[-1]
    if window[h2].high <= window[h1].high:                         # no higher high
        return None
    prior_lows = [i for i in lows if i < h2]
    if not prior_lows:
        return None
    swing_low = window[prior_lows[-1]].low
    pullback = window[h2 + 1:]
    if len(pullback) < 2:
        return None
    pb_low = min(b.low for b in pullback)
    if pb_low <= swing_low:                                        # not a higher low
        return None
    sig, prev = bars[-1], bars[-2]
    rng = sig.high - sig.low
    if not (sig.close > prev.high and sig.close > sig.open and rng > 0
            and sig.close >= sig.low + 0.5 * rng):
        return None
    return pb_low - cfg.stop_buffer * avg_range(window)


class ScalpPriceAction(DirectionalStrategy):
    name = "SCALP"
    entry_tag = "SCALP"

    def __init__(self, cfg: ScalpConfig = ScalpConfig(),
                 sentiment_cfg: SentimentConfig = SentimentConfig()) -> None:
        super().__init__(cfg.risk_per_trade, cfg.max_position_value, cfg.target_r,
                         cfg.max_trades_per_symbol, cfg.first_entry, cfg.last_entry,
                         cfg.max_hold_minutes, sentiment_cfg, rules_from(cfg))
        self.cfg = cfg

    def params(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, time) else v)
                for k, v in self.cfg.__dict__.items()}

    def signal(self, sym, bars, k, ctx, direction):
        avg = avg_range(bars[-1 - self.cfg.range_lookback:-1])
        for name, rule in (("TIGHT_BREAKOUT", tight_breakout), ("HIGHER_LOW", higher_low)):
            stop = rule(bars, self.cfg)
            if stop is None:
                continue
            ref = bars[-1].close
            risk = ref - stop
            if risk <= 0 or (avg and risk > self.cfg.max_risk_mult * avg):
                continue
            return name, ref, risk
        return None
