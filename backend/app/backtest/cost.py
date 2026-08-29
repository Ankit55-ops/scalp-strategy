"""Realistic trading cost model: spread, commission, slippage, swap."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostParams:
    spread_pips: float
    commission_per_lot: float = 0.0  # per standard lot per round trip (account CCY)
    slippage_pips: float = 0.0
    swap_pips_per_night: float = 0.0
    contract_size: float = 100000.0
    pip_size: float = 0.0001


@dataclass
class CostBreakdown:
    spread_cost: float
    slippage_cost: float
    commission: float
    swap: float
    total: float

    def as_dict(self) -> dict:
        return {
            "spread_cost": round(self.spread_cost, 4),
            "slippage_cost": round(self.slippage_cost, 4),
            "commission": round(self.commission, 4),
            "swap": round(self.swap, 4),
            "total": round(self.total, 4),
        }
