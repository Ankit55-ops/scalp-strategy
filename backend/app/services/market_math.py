"""Forex market math: pip values, position sizing, risk-per-trade, costs."""

from __future__ import annotations

from dataclasses import dataclass


def pip_size(quote_currency: str, pip_position: int | None = None) -> float:
    """Return the numeric size of one pip for a symbol.

    JPY pairs quote to 3 decimals for pips; most others 4.
    `pip_position` may be given explicitly; otherwise inferred from quote CCY.
    """
    if pip_position is not None:
        return 10 ** (-pip_position)
    if quote_currency.upper() == "JPY":
        return 0.01
    return 0.0001


def price_to_pips(price_diff: float, pip_size: float) -> float:
    return price_diff / pip_size if pip_size else 0.0


def pips_to_price(pips: float, pip_size: float) -> float:
    return pips * pip_size


@dataclass
class PositionSizeResult:
    size_units: float
    risk_amount: float
    stop_distance_price: float
    stop_distance_pips: float
    pnl_per_pip: float


def position_size(
    account_balance: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_price: float,
    pip_size: float,
    pnl_per_pip_per_unit: float,
    contract_size: float = 100000.0,
) -> PositionSizeResult:
    """Compute position size such that the loss to the stop equals risk.

    pnl_per_pip_per_unit is the P&L per pip per 1 unit of base (usually
    1/quote-to-base factor, but approximated as 1 for USD accounts; for the
    USD quote pairs pnl per pip per standard lot = pip_size * 100000).
    """
    risk_amount = account_balance * (risk_per_trade_pct / 100.0)
    stop_distance_price = abs(entry_price - stop_price)
    if stop_distance_price <= 0:
        raise ValueError("stop distance must be > 0")
    stop_distance_pips = stop_distance_price / pip_size
    pnl_per_pip = pnl_per_pip_per_unit * contract_size
    risked_pips_pnl = pnl_per_pip * stop_distance_pips
    if risked_pips_pnl <= 0:
        raise ValueError("risk-to-stop P&L must be > 0")
    size_units = risk_amount / risked_pips_pnl * contract_size
    return PositionSizeResult(
        size_units=size_units,
        risk_amount=risk_amount,
        stop_distance_price=stop_distance_price,
        stop_distance_pips=stop_distance_pips,
        pnl_per_pip=pnl_per_pip,
    )


def spread_in_pips(bid: float, ask: float, pip_size: float) -> float:
    return (ask - bid) / pip_size


def normalized_symbol(canonical: str, provider_symbol: str) -> str:
    """Normalize a provider-specific symbol to canonical form (strip suffix)."""
    base = provider_symbol
    base = base.replace("/", "")
    base = base.split(".")[0]
    base = base.rstrip("mM")
    return base.upper()


def symbol_variant(canonical: str, suffix: str | None = None) -> str:
    """Produce a provider-style variant of a canonical symbol."""
    c = canonical.upper().replace("/", "")
    if suffix:
        return f"{c}{suffix}"
    return c
