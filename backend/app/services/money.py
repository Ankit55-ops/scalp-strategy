"""Decimal-backed money math for the paper-trading ledger.

All balance, cost, and P&L arithmetic runs on ``Decimal`` with explicit
rounding so floating-point drift and non-finite values (NaN/Inf) can never
poison an account. Storage stays as rounded ``float`` columns in this release;
migrating the columns to ``NUMERIC`` is tracked as a follow-up.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

_MONEY_PLACES = 2
_QTY_PLACES = 6


def d(value) -> Decimal:
    """Coerce a float/int/str/Decimal to a finite Decimal."""
    if value is None:
        raise ValueError("money value is None")
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid money value: {value!r}") from exc
    if not dec.is_finite():
        raise ValueError(f"non-finite money value: {value!r}")
    return dec


def _round(dec: Decimal, places: int) -> Decimal:
    return dec.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def to_money(value) -> str:
    """Decimal rounded to 2 places, returned as a canonical string."""
    return format(_round(d(value), _MONEY_PLACES), "f")


def to_float(value, places: int = _MONEY_PLACES) -> float:
    return float(_round(d(value), places))


def add(*values) -> float:
    total = sum((d(v) for v in values), Decimal("0"))
    return float(_round(total, _MONEY_PLACES))


def sub(a, b) -> float:
    return float(_round(d(a) - d(b), _MONEY_PLACES))


def mul(a, b, places: int = _QTY_PLACES) -> float:
    return float(_round(d(a) * d(b), places))


def div(a, b, places: int = _QTY_PLACES) -> float:
    return float(_round(d(a) / d(b), places))