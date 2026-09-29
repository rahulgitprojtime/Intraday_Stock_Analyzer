"""Transaction cost model — DECISIONS #29. SIMULATION ONLY.

Charges for one executed equity-intraday order, every rate from
`config/costs.yaml` (Groww's published pricing). Each component is kept
separately so reports can show where the money went. Amounts are rounded
to 4 dp; contract-note rupee rounding is not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Charges:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_fee: float
    ipft: float
    gst: float
    stamp_duty: float

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange_txn + self.sebi_fee + self.ipft
                     + self.gst + self.stamp_duty, 4)


@dataclass(frozen=True)
class CostModel:
    brokerage_flat: float
    brokerage_pct: float
    brokerage_min: float
    stt_sell_pct: float
    stamp_duty_buy_pct: float
    exchange_txn_pct: dict
    sebi_fee_pct: float
    ipft_pct: dict
    gst_pct: float

    @classmethod
    def from_dict(cls, d: dict) -> CostModel:
        b = d["brokerage"]
        return cls(float(b["flat_per_order"]), float(b["pct"]), float(b["min_per_order"]),
                   float(d["stt_sell_pct"]), float(d["stamp_duty_buy_pct"]),
                   {k: float(v) for k, v in d["exchange_txn_pct"].items()},
                   float(d["sebi_fee_pct"]),
                   {k: float(v) for k, v in (d.get("ipft_pct") or {}).items()},
                   float(d["gst_pct"]))

    def charges(self, side: str, quantity: int, price: float, exchange: str = "NSE") -> Charges:
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        turnover = quantity * price
        pct = lambda rate: turnover * rate / 100  # noqa: E731
        brokerage = max(self.brokerage_min, min(self.brokerage_flat, pct(self.brokerage_pct)))
        exchange_txn = pct(self.exchange_txn_pct[exchange])
        sebi = pct(self.sebi_fee_pct)
        ipft = pct(self.ipft_pct.get(exchange, 0.0))
        gst = (brokerage + exchange_txn + sebi + ipft) * self.gst_pct / 100
        r = lambda x: round(x, 4)  # noqa: E731
        return Charges(
            brokerage=r(brokerage),
            stt=r(pct(self.stt_sell_pct)) if side == "SELL" else 0.0,
            exchange_txn=r(exchange_txn), sebi_fee=r(sebi), ipft=r(ipft), gst=r(gst),
            stamp_duty=r(pct(self.stamp_duty_buy_pct)) if side == "BUY" else 0.0,
        )
