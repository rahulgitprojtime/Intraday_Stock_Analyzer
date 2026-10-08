"""Per-symbol data quality — M6 (DECISIONS #14).

Critical inputs (fresh candles, prep) missing or stale → the symbol is not
scored. Non-critical gaps are listed in `missing_inputs` and the affected
component becomes unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from src.data.models import Candle
from src.recommendation.models import DataQuality


def assess_data_quality(
    bars: Sequence[Candle],
    as_of: datetime,
    stale_after_seconds: float,
    *,
    has_prep: bool,
    has_volume_curve: bool,
    has_index: bool,
) -> DataQuality:
    done = [c for c in bars if c.is_complete]
    missing = [name for name, ok in (("candles", bool(done)), ("prep", has_prep),
                                     ("volume_curve", has_volume_curve), ("index", has_index))
               if not ok]
    ts = age = None
    stale = False
    if done:
        last = done[-1]
        ts = last.timestamp.isoformat()
        age = (as_of - (last.timestamp + timedelta(minutes=last.timeframe_minutes))).total_seconds()
        stale = age > stale_after_seconds
    if not done or not has_prep:
        status = "INCOMPLETE"
    elif stale:
        status = "STALE"
    else:
        status = "OK"
    return DataQuality(status, ts, age, stale, tuple(missing))
