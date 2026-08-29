"""Paper broker: simulated fills priced on real bid/ask quotes.

Long entries pay the ask plus positive slippage; long exits receive the bid
minus negative slippage. Shorts mirror this: enter at bid minus slippage, exit
at ask plus slippage. Cost accounting tracks spread, slippage, and commission
separately so net P&L is fully decomposed for analytics.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.money import d, mul, to_float

PAPER_LOT_SIZE = 100000.0
DEFAULT_SLIPPAGE_PIPS = 0.1
DEFAULT_COMMISSION_PER_LOT = 7.0  # USD per 100k per side


@dataclass(frozen=True)
class BrokerCosts:
    spread_cost: float
    slippage_cost: float
    commission: float

    @property
    def total(self) -> float:
        from app.services.money import add

        return add(self.spread_cost, self.slippage_cost, self.commission)


class PaperBroker:
    name = "paper"

    def __init__(
        self,
        slippage_pips: float = DEFAULT_SLIPPAGE_PIPS,
        commission_per_lot: float = DEFAULT_COMMISSION_PER_LOT,
    ) -> None:
        self.slippage_pips = float(slippage_pips)
        self.commission_per_lot = float(commission_per_lot)

    def entry_price(self, quote: dict, side: str) -> float:
        pip = _pip(quote)
        slp = self.slippage_pips * pip
        if side == "short":
            return to_float(d(quote["bid"]) - d(slp), 6)
        return to_float(d(quote["ask"]) + d(slp), 6)

    def exit_price(self, quote: dict, side: str) -> float:
        pip = _pip(quote)
        slp = self.slippage_pips * pip
        if side == "short":
            return to_float(d(quote["ask"]) + d(slp), 6)
        return to_float(d(quote["bid"]) - d(slp), 6)

    def costs(self, quote: dict, side: str, size_units: float, entry_ts: float = 0.0) -> BrokerCosts:
        pip = _pip(quote)
        spread_pips = float(quote.get("spread_pips") or 0.0)
        spread_cost = mul(spread_pips, mul(pip, size_units, places=8), places=6)
        slp = mul(self.slippage_pips, mul(pip, size_units, places=8), places=6)
        # Commission: per lot per side, applied as a round trip here since the
        # filled order represents the full position lifecycle.
        commission = mul(mul(size_units / PAPER_LOT_SIZE, self.commission_per_lot, places=8), 2, places=6)
        return BrokerCosts(spread_cost=spread_cost, slippage_cost=slp, commission=commission)

    @staticmethod
    def gross_pnl(side: str, entry_price: float, exit_price: float, size_units: float) -> float:
        from app.services.money import to_float

        raw = (d(entry_price) - d(exit_price)) * d(size_units)
        if side != "short":
            raw = -raw
        return to_float(raw, 6)

    @staticmethod
    def pips(side: str, entry_price: float, exit_price: float, quote_pip) -> float:
        from app.services.money import to_float

        raw = (d(entry_price) - d(exit_price)) / d(quote_pip)
        if side != "short":
            raw = -raw
        return to_float(raw, 4)


def _pip(quote: dict) -> float:
    symbol = str(quote.get("symbol", "")).upper().replace("/", "")
    if symbol.endswith("JPY"):
        return 0.01
    if quote.get("spread_price") and quote.get("spread_pips"):
        sp = float(quote.get("spread_price"))
        spps = float(quote.get("spread_pips"))
        if sp > 0 and spps > 0:
            return sp / spps
    return 0.0001