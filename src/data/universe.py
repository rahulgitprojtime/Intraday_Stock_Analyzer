"""Universe resolution — M2.

Turns `config/universe.yaml` symbol lists into resolved `Instrument`s.
Broker-agnostic: takes any `resolve(symbol, exchange) -> Instrument`
callable (in practice `BrokerAdapter.resolve_instrument` bound to CASH).
Liquidity filtering needs historical data and happens later (M6); this
step only answers "which configured symbols exist and are eligible?".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.data.models import Instrument

Resolver = Callable[[str, str], Instrument]


@dataclass
class ResolvedUniverse:
    stocks: list[Instrument] = field(default_factory=list)
    indices: list[Instrument] = field(default_factory=list)
    # symbol -> reason; surfaced rather than silently dropped
    rejected: dict[str, str] = field(default_factory=dict)


def resolve_universe(config: dict, resolve: Resolver) -> ResolvedUniverse:
    exchange = config.get("exchange", "NSE")
    allowed_series = set(config.get("allowed_series") or [])
    require_intraday = bool(config.get("require_intraday"))
    max_size = config.get("filters", {}).get("max_universe_size")
    out = ResolvedUniverse()
    seen: set[str] = set()

    for symbol in config.get("symbols") or []:
        symbol = symbol.strip().upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        try:
            inst = resolve(symbol, exchange)
        except ValueError as exc:
            out.rejected[symbol] = str(exc)
            continue
        if inst.is_index:
            out.rejected[symbol] = "is an index, not a stock (list it under indices)"
        elif allowed_series and inst.series not in allowed_series:
            out.rejected[symbol] = f"series {inst.series!r} not in allowed_series"
        elif require_intraday and inst.is_intraday is False:
            out.rejected[symbol] = "intraday (MIS) not allowed"
        elif max_size and len(out.stocks) >= max_size:
            out.rejected[symbol] = f"exceeds max_universe_size={max_size}"
        else:
            out.stocks.append(inst)

    for symbol in config.get("indices") or []:
        try:
            inst = resolve(symbol.strip().upper(), exchange)
        except ValueError as exc:
            out.rejected[symbol] = str(exc)
            continue
        if not inst.is_index:
            out.rejected[symbol] = "not an index"
        else:
            out.indices.append(inst)
    return out
